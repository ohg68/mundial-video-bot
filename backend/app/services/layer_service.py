import os
import json
import logging
import httpx
import asyncio
import subprocess
from pathlib import Path
from app.models.project import ProjectConfig, VideoSource, LayerStatus
from app.services import project_service, photo_sources
from app.services.script_utils import clean_script

log = logging.getLogger(__name__)
LOCAL_CLIPS_DIR = Path(os.getenv("LOCAL_CLIPS_DIR", "clips"))

# Voz de Google TTS (proveedor opcional). WaveNet masculina en español.
# Gratis hasta 1 millón de caracteres/mes. Cambiable con GOOGLE_TTS_VOICE.
GOOGLE_TTS_VOICE = os.getenv("GOOGLE_TTS_VOICE", "es-ES-Wavenet-B")
GOOGLE_TTS_LANG = os.getenv("GOOGLE_TTS_LANG", "es-ES")

# Ruta donde recreamos el archivo de credenciales a partir de la variable
# de entorno GOOGLE_CREDENTIALS_JSON (Railway no permite subir archivos).
_GOOGLE_CRED_PATH = Path("/tmp/google_credentials.json")


def _ensure_google_credentials():
    """Recrea el archivo de credenciales de Google desde la variable de entorno.

    En Railway pegamos el contenido del JSON en GOOGLE_CREDENTIALS_JSON. Aquí lo
    volcamos a un archivo y apuntamos GOOGLE_APPLICATION_CREDENTIALS hacia él,
    que es lo que la librería de Google busca para autenticarse.
    """
    if os.getenv("GOOGLE_APPLICATION_CREDENTIALS") and Path(
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
    ).exists():
        return  # ya configurado

    raw = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if not raw:
        raise RuntimeError(
            "Falta la variable GOOGLE_CREDENTIALS_JSON con el contenido del archivo "
            "de credenciales de Google."
        )
    # Validar que sea JSON correcto antes de escribirlo.
    json.loads(raw)
    _GOOGLE_CRED_PATH.write_text(raw)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(_GOOGLE_CRED_PATH)


async def generate_script(project_id: str, config: ProjectConfig) -> str:
    project_service.update_layer_status(project_id, "audio", LayerStatus.pending)

    # Contexto extra (partido/fecha) solo si el proyecto lo trae; no forzar deporte.
    extra = ""
    if config.match:
        extra += f"\nPartido/evento: {config.match}"
    if config.match_date:
        extra += f"\nFecha: {config.match_date}"

    prompt = f"""Eres un guionista de videos cortos para redes sociales (YouTube Shorts, TikTok, Reels).
Genera un guion en español para un video corto de ~90 segundos sobre el siguiente tema.

Título: {config.title}
Tema: {config.topic}{extra}

Importante:
- Adáptate EXACTAMENTE al tema indicado. No inventes ni mezcles otros temas.
- No asumas que es sobre fútbol ni el Mundial salvo que el tema lo diga explícitamente.

El guion debe:
- Tener un gancho potente en los primeros 5 segundos
- Ser informativo, claro y atractivo
- Terminar con una llamada a la acción (suscríbete, comenta)
- Durar aproximadamente 90 segundos al leerlo en voz alta
- Contener solo el texto que leerá el narrador, sin indicaciones de escena ni acotaciones

Responde SOLO con el guion, sin introducción ni explicación."""

    deepseek_key = os.getenv("DEEPSEEK_API_KEY")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.deepseek.com/chat/completions",
            headers={
                "Authorization": f"Bearer {deepseek_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek-chat",
                "max_tokens": 1000,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
    data = resp.json()
    if resp.status_code != 200 or "choices" not in data:
        raise RuntimeError(f"DeepSeek error (status {resp.status_code}): {data}")
    script = data["choices"][0]["message"]["content"]
    script = clean_script(script)  # quitar encabezados/acotaciones del LLM

    # Persistir en SQLite (project.json es efímero en Railway y nadie lo lee)
    project_service.update_project_config(project_id, {"script": script})
    return script


async def generate_audio(project_id: str, config: ProjectConfig) -> Path:
    project_service.update_layer_status(project_id, "audio", LayerStatus.pending)
    script = config.script or await generate_script(project_id, config)
    script = clean_script(script)  # defensa: solo texto narrable al TTS
    output_path = project_service.get_layer_path(project_id, "audio")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    voice = config.audio.voice.value if config.audio.custom_file is None else None
    provider = getattr(config.audio, "tts_provider", None)
    provider = provider.value if hasattr(provider, "value") else provider

    if config.audio.custom_file:
        import shutil
        shutil.copy2(config.audio.custom_file, output_path)
    elif provider == "google":
        # Google TTS (heredado de main): requiere GOOGLE_CREDENTIALS_JSON en Railway.
        _ensure_google_credentials()
        voice = getattr(config.audio, "voice_name", None) or GOOGLE_TTS_VOICE
        speaking_rate = max(0.25, min(4.0, float(config.audio.speed or 1.0)))

        def _synthesize():
            from google.cloud import texttospeech
            client = texttospeech.TextToSpeechClient()
            synthesis_input = texttospeech.SynthesisInput(text=script)
            voice_params = texttospeech.VoiceSelectionParams(
                language_code=GOOGLE_TTS_LANG,
                name=voice,
            )
            audio_config = texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3,
                speaking_rate=speaking_rate,
            )
            resp = client.synthesize_speech(
                input=synthesis_input, voice=voice_params, audio_config=audio_config
            )
            return resp.audio_content

        loop = asyncio.get_event_loop()
        try:
            audio_bytes = await loop.run_in_executor(None, _synthesize)
        except Exception as e:
            raise RuntimeError(f"Google TTS error: {e}")
        if not audio_bytes:
            raise RuntimeError("Google TTS no devolvio audio")

        # Guardar el mp3 crudo y re-encodear a mp3 estandar con ffmpeg.
        tmp_path = output_path.with_suffix(".raw.mp3")
        tmp_path.write_bytes(audio_bytes)
        conv = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", str(tmp_path),
            "-acodec", "libmp3lame", "-q:a", "2",
            str(output_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await conv.communicate()
        tmp_path.unlink(missing_ok=True)
    else:
        # edge-tts generates webm/opus internally; save to temp then convert to mp3
        tmp_path = output_path.with_suffix(".tmp.mp3")
        proc = await asyncio.create_subprocess_exec(
            "edge-tts",
            "--voice", voice,
            "--rate", f"+{int((config.audio.speed - 1) * 100)}%",
            "--text", script,
            "--write-media", str(tmp_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        # Convert to proper mp3 with ffmpeg
        conv = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", str(tmp_path),
            "-acodec", "libmp3lame", "-q:a", "2",
            str(output_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await conv.communicate()
        tmp_path.unlink(missing_ok=True)

    project_service.update_layer_status(project_id, "audio", LayerStatus.ready, {
        "voice": voice or "custom",
        "file": str(output_path),
    })
    return output_path


def _fmt_ts(t: float) -> str:
    """Segundos -> formato SRT 'HH:MM:SS,mmm'."""
    if t < 0:
        t = 0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    if ms == 1000:
        ms = 0
        s += 1
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _normalize_word(w: str) -> str:
    import re
    return re.sub(r"[^\w]", "", w.lower())


def _align_script_to_times(script_words, whisper_words):
    """Asigna a cada palabra del GUION (ortografía correcta) el tiempo de la
    palabra de Whisper que le corresponde. Así los subtítulos tienen la
    ortografía del guion y los tiempos reales del audio.

    script_words: lista de palabras correctas (del guion).
    whisper_words: lista de (texto, start, end) de Whisper.
    Devuelve: lista de (palabra_correcta, start, end).
    """
    import difflib
    w_norm = [_normalize_word(w[0]) for w in whisper_words]
    s_norm = [_normalize_word(w) for w in script_words]
    sm = difflib.SequenceMatcher(None, s_norm, w_norm)
    result = [None] * len(script_words)

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                wt = whisper_words[j1 + k]
                result[i1 + k] = (script_words[i1 + k], wt[1], wt[2])
        else:
            if j2 > j1:
                t_start = whisper_words[j1][1]
                t_end = whisper_words[j2 - 1][2]
            else:
                ref = min(j1, len(whisper_words) - 1)
                t_start = whisper_words[ref][1] if whisper_words else 0.0
                t_end = t_start + 0.5
            count = max(1, i2 - i1)
            step = (t_end - t_start) / count
            for k in range(i2 - i1):
                result[i1 + k] = (
                    script_words[i1 + k],
                    t_start + k * step,
                    t_start + (k + 1) * step,
                )
    for idx in range(len(result)):
        if result[idx] is None:
            prev = result[idx - 1][2] if idx > 0 and result[idx - 1] else 0.0
            result[idx] = (script_words[idx], prev, prev + 0.3)
    return result


def _build_srt_from_words(words, words_per_cue: int = 5) -> str:
    """Convierte palabras-con-tiempo (de Whisper) en un .srt agrupado.

    words: lista de tuplas (texto, inicio_seg, fin_seg).
    """
    lines = []
    idx = 1
    i = 0
    n = len(words)
    while i < n:
        group = words[i:i + words_per_cue]
        if not group:
            break
        start = group[0][1]
        end = group[-1][2]
        text = " ".join(w[0] for w in group)
        lines.append(f"{idx}\n{_fmt_ts(start)} --> {_fmt_ts(end)}\n{text}\n")
        i += words_per_cue
        idx += 1
    return "\n".join(lines)


# Modelo Whisper cargado una sola vez y reutilizado (evita recargar en cada video).
_whisper_model = None


def _get_whisper():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        size = os.getenv("WHISPER_MODEL", "base")
        _whisper_model = WhisperModel(size, device="cpu", compute_type="int8")
    return _whisper_model


def _subtitles_with_whisper(audio_path: Path, script: str = "") -> str:
    """Escucha el audio con Whisper y devuelve un .srt con tiempos reales.

    Si se pasa el guion, usa la ortografía del guion con los tiempos de Whisper
    (corrige los errores de transcripción de Whisper, p.ej. nombres propios).
    Devuelve cadena vacía si algo falla (el llamador hará fallback).
    """
    try:
        model = _get_whisper()
        segments, info = model.transcribe(
            str(audio_path), language="es", word_timestamps=True
        )
        words = []
        for seg in segments:
            for w in (seg.words or []):
                txt = w.word.strip()
                if txt:
                    words.append((txt, float(w.start), float(w.end)))
        if not words:
            return ""
        # Si tenemos el guion, sustituir las palabras de Whisper por las del
        # guion (ortografía correcta) manteniendo los tiempos.
        script_words = script.split() if script else []
        if script_words:
            aligned = _align_script_to_times(script_words, words)
            return _build_srt_from_words(aligned)
        return _build_srt_from_words(words)
    except Exception:
        return ""


def _voice_range(path: Path, total: float) -> tuple:
    """Detecta el rango (inicio, fin) donde realmente hay voz, quitando silencios.

    Usa el filtro silencedetect de ffmpeg. Si no detecta nada, devuelve el
    audio completo. Esto evita que los subtítulos arranquen en 0 cuando el
    audio tiene un pequeño silencio inicial, reduciendo el desfase.
    """
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
             "-af", "silencedetect=noise=-35dB:d=0.3", "-f", "null", "-"],
            capture_output=True, text=True,
        )
        err = proc.stderr
        starts = []
        ends = []
        for line in err.splitlines():
            if "silence_end" in line:
                try:
                    starts.append(float(line.split("silence_end:")[1].split("|")[0].strip()))
                except Exception:
                    pass
            if "silence_start" in line:
                try:
                    ends.append(float(line.split("silence_start:")[1].strip()))
                except Exception:
                    pass
        # Inicio de voz = primer fin de silencio (si el audio empieza en silencio).
        voice_start = starts[0] if starts else 0.0
        # Fin de voz = último inicio de silencio (si el audio termina en silencio).
        voice_end = ends[-1] if ends else total
        # Sanidad: que el rango tenga sentido.
        if voice_end <= voice_start or voice_end > total or voice_start < 0:
            return (0.0, total)
        # No recortar de más: dejar un pequeño margen.
        return (max(0.0, voice_start - 0.1), min(total, voice_end + 0.1))
    except Exception:
        return (0.0, total)


def _build_srt_by_duration(text: str, total_seconds: float, words_per_cue: int = 6,
                           voice_start: float = 0.0, voice_end: float = None) -> str:
    """Reparte el texto en subtitulos dentro del rango de voz, ponderando por
    longitud de palabra (las palabras largas duran mas). Mas preciso que un
    reparto uniforme, aunque no tan exacto como Whisper.
    """
    words = text.split()
    if not words or total_seconds <= 0:
        return ""
    if voice_end is None or voice_end <= voice_start:
        voice_end = total_seconds
    span = voice_end - voice_start
    if span <= 0:
        span = total_seconds
        voice_start = 0.0

    weights = [max(1, len(w)) for w in words]
    total_w = sum(weights)
    # tiempo de borde de cada palabra segun peso acumulado
    times = [voice_start]
    acc = 0
    for w in weights:
        acc += w
        times.append(voice_start + (acc / total_w) * span)

    lines = []
    idx = 1
    i = 0
    n = len(words)
    while i < n:
        j = min(i + words_per_cue, n)
        start = times[i]
        end = times[j]
        lines.append(f"{idx}\n{_fmt_ts(start)} --> {_fmt_ts(end)}\n{' '.join(words[i:j])}\n")
        i = j
        idx += 1
    return "\n".join(lines)


def _audio_duration(path: Path) -> float:
    """Duracion del audio en segundos via ffprobe."""
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)]
        )
        return float(out.decode().strip())
    except Exception:
        return 0.0


async def generate_subtitles(project_id: str, config: ProjectConfig = None) -> Path:
    project_service.update_layer_status(project_id, "subtitles", LayerStatus.pending)
    output_path = project_service.get_layer_path(project_id, "subtitles")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Obtener guión y voz reales (de config si viene, si no del proyecto guardado)
    if config is not None:
        script = config.script
        voice = config.audio.voice.value
        speed = config.audio.speed
    else:
        meta = project_service.get_project(project_id)
        cfg = meta.get("config", {}) if meta else {}
        script = cfg.get("script")
        audio_cfg = cfg.get("audio", {})
        voice = audio_cfg.get("voice") or "es-ES-AlvaroNeural"
        speed = audio_cfg.get("speed", 1.0)

    if not script:
        project_service.update_layer_status(project_id, "subtitles", LayerStatus.error, {
            "error": "No hay guión — genera el guión antes que los subtítulos.",
        })
        raise RuntimeError("No hay guión para generar subtítulos")

    script = clean_script(script)  # defensa: solo texto narrable a los subtítulos

    # 1) Whisper sobre el audio ya generado: tiempos reales por palabra con la
    #    ortografía del guion. 2) Si Whisper falla, reparto ponderado dentro del
    #    rango de voz detectado. 3) Sin audio aún: edge-tts (más abajo).
    audio_path = project_service.get_layer_path(project_id, "audio")
    if audio_path.exists():
        srt = _subtitles_with_whisper(audio_path, script=script)
        if not srt:
            dur = _audio_duration(audio_path)
            if dur > 0:
                v_start, v_end = _voice_range(audio_path, dur)
                srt = _build_srt_by_duration(script, dur, voice_start=v_start, voice_end=v_end)
        if srt:
            output_path.write_text(srt, encoding="utf-8")
            project_service.update_layer_status(project_id, "subtitles", LayerStatus.ready, {
                "file": str(output_path),
                "synced_with": "audio",
            })
            return output_path

    # Fallback sin audio: edge-tts genera subtítulos SRT sincronizados con su
    # propia síntesis; requiere --write-media (descartable)
    tmp_media = output_path.with_suffix(".sub.mp3")
    proc = await asyncio.create_subprocess_exec(
        "edge-tts",
        "--voice", voice,
        "--rate", f"+{int((speed - 1) * 100)}%",
        "--text", script,
        "--write-media", str(tmp_media),
        "--write-subtitles", str(output_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    tmp_media.unlink(missing_ok=True)

    if proc.returncode != 0 or not output_path.exists():
        project_service.update_layer_status(project_id, "subtitles", LayerStatus.error, {
            "error": f"edge-tts falló: {stderr.decode()[-200:]}",
        })
        raise RuntimeError(f"Error generando subtítulos: {stderr.decode()[-200:]}")

    project_service.update_layer_status(project_id, "subtitles", LayerStatus.ready, {
        "file": str(output_path),
    })
    return output_path


async def generate_scene_plan(project_id: str, config: ProjectConfig, scene_count: int = 6) -> list:
    """Segmenta el GUION en tramos narrativos y elige keywords visuales para cada
    tramo según lo que se dice ahí. Así la imagen pega con la narración.

    Devuelve: [{"line": str, "visual_1": str, "visual_2": str}, ...]
    El campo 'line' es solo para depurar/log; el render usa visual_1/visual_2.
    """
    import json

    # 1) Recuperar el guion ya generado (no lo regeneramos)
    script = config.script
    if not script:
        meta = project_service.get_project(project_id)
        script = (meta or {}).get("config", {}).get("script", "")
    if not script:
        # Sin guion no hay encaje posible: caemos al modo topic-only
        script = config.topic

    # 2) Pedir a DeepSeek que segmente EL GUION y derive visuales de cada tramo
    prompt = f"""Eres director de fotografía. Te doy el GUION de un vídeo vertical (9:16).
Divídelo en EXACTAMENTE {scene_count} tramos consecutivos que cubran TODO el guion en orden.
Para cada tramo, elige DOS keywords visuales en inglés para buscar stock footage en Pexels:
- visual_1: el plano que ilustra LITERALMENTE lo que se dice en ese tramo
- visual_2: un plano complementario distinto, también relacionado con ese tramo
Las keywords deben derivar del CONTENIDO de cada tramo, no del tema general.
Sé concreto y filmable (objetos, acciones, lugares reales).

GUION:
\"\"\"{script}\"\"\"

Responde SOLO con un array JSON válido, sin markdown ni texto extra:
[{{"line":"<fragmento del guion>","visual_1":"...","visual_2":"..."}}, ...]"""

    deepseek_key = os.getenv("DEEPSEEK_API_KEY")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {deepseek_key}",
                     "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "max_tokens": 1200,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=40,
        )
    data = resp.json()
    if resp.status_code != 200 or "choices" not in data:
        raise RuntimeError(f"DeepSeek scene-plan error ({resp.status_code}): {data}")

    raw = data["choices"][0]["message"]["content"].strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    # Robustez: DeepSeek a veces añade prosa antes/después del array (a veces con
    # corchetes propios). Arrancamos en el primer '[' y usamos raw_decode, que
    # parsea SOLO el primer valor JSON válido e ignora cualquier texto sobrante.
    start = raw.find("[")
    if start == -1:
        raise RuntimeError(f"DeepSeek scene-plan: respuesta sin array JSON: {raw[:200]}")
    scenes, _ = json.JSONDecoder().raw_decode(raw[start:])

    clean = []
    for s in scenes[:scene_count]:
        v1 = (s.get("visual_1") or config.topic).strip()
        v2 = (s.get("visual_2") or v1).strip()
        clean.append({"line": (s.get("line") or "").strip(), "visual_1": v1, "visual_2": v2})

    # Si DeepSeek devolvió menos tramos de los pedidos, rellenar con el último
    while len(clean) < scene_count and clean:
        clean.append(clean[-1])
    return clean


async def fetch_pexels_clips(query: str, count: int = 8) -> list:
    pexels_key = os.getenv("PEXELS_API_KEY")
    if not pexels_key:
        log.warning("PEXELS_API_KEY not set — skipping Pexels video clips")
        return []
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.pexels.com/videos/search",
                headers={"Authorization": pexels_key},
                params={"query": query, "per_page": count, "orientation": "portrait"},
                timeout=15,
            )
        data = resp.json()
        urls = []
        for v in data.get("videos", []):
            files = [f for f in v.get("video_files", []) if f.get("link", "").endswith(".mp4")]
            # Prefer HD (≤1920px). Avoid 4K/UHD — too large for server processing.
            hd = next((f for f in files if f.get("quality") == "hd"), None)
            reasonable = next(
                (f for f in sorted(files, key=lambda f: f.get("width", 0) * f.get("height", 0), reverse=True)
                 if max(f.get("width", 0), f.get("height", 0)) <= 1920),
                None
            )
            best = hd or reasonable or (files[0] if files else None)
            if best and best.get("link"):
                urls.append(best["link"])
        return urls
    except Exception as e:
        log.warning(f"Pexels clips error: {e}")
        return []


async def _download_clips(urls: list, dest_dir: Path, prefix: str) -> list:
    """Descarga clips en paralelo, validando status_code. Devuelve paths válidos."""
    dest_dir.mkdir(parents=True, exist_ok=True)

    async def _one(i: int, url: str):
        dest = dest_dir / f"{prefix}_{i}.mp4"
        if dest.exists() and dest.stat().st_size > 10000:
            return dest
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
                r = await client.get(url)
            if r.status_code != 200:
                log.warning(f"Clip {prefix}_{i} HTTP {r.status_code}: {url[:80]}")
                return None
            if len(r.content) < 10000:
                log.warning(f"Clip {prefix}_{i} demasiado pequeño ({len(r.content)}B) — descartado")
                return None
            dest.write_bytes(r.content)
            return dest
        except Exception as e:
            log.warning(f"Error descargando clip {prefix}_{i}: {e}")
            return None

    results = await asyncio.gather(*[_one(i, u) for i, u in enumerate(urls)])
    return [r for r in results if r is not None]


async def _get_audio_duration(path: Path) -> float:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return float(stdout.decode().strip())
    except Exception:
        return 0.0


async def assemble_video_layer(project_id: str, config: ProjectConfig) -> Path:
    # Clips propios subidos por el usuario (endpoint /upload-clips): tienen
    # prioridad sobre cualquier fuente (Pexels, fotos, A/B) y se cortan por
    # escena según la duración del audio.
    user_clips_dir = Path("projects") / project_id / "video" / "user_clips"
    user_clips: list[Path] = []
    if user_clips_dir.exists():
        for ext in ("*.mp4", "*.mov", "*.MP4", "*.MOV", "*.webm", "*.mkv"):
            user_clips.extend(sorted(user_clips_dir.glob(ext)))
    if user_clips:
        return await _assemble_from_user_clips(project_id, config, user_clips)

    # A/B split: las imágenes siguen el guion (2 visuales por escena). Requiere el
    # guion ya generado; el orquestador corre script→audio antes que la capa de video.
    if config.video.ab_split:
        return await assemble_video_layer_ab(project_id, config)

    project_service.update_layer_status(project_id, "video", LayerStatus.pending)
    output_path = project_service.get_layer_path(project_id, "video")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    clips: list[Path] = []
    aspect = config.aspect

    # ── Photo-based sources ────────────────────────────────────────
    if config.video.source in (VideoSource.photos, VideoSource.mixed_photos):
        from app.websocket import manager

        n_photos = 6 if config.video.source == VideoSource.photos else 3

        # Match per-clip duration to audio length when available
        clip_dur = float(config.video.clip_duration or 4)
        audio_path = project_service.get_layer_path(project_id, "audio")
        if audio_path.exists() and config.video.source == VideoSource.photos:
            audio_dur = await _get_audio_duration(audio_path)
            if audio_dur > 0:
                clip_dur = round(audio_dur / n_photos, 2)

        async def _on_progress(data):
            await manager.send_progress(project_id, data)

        photo_clips_dir = Path("projects") / project_id / "video" / "photo_clips"
        photo_clips = await photo_sources.fetch_photo_clips(
            query=config.topic,
            dest_dir=photo_clips_dir,
            count=n_photos,
            duration=clip_dur,
            aspect=aspect,
            on_progress=_on_progress,
        )

        pexels_query = " ".join(config.topic.split()[:4])
        dl_dir = Path("projects") / project_id / "video" / "downloads"
        dl_dir.mkdir(parents=True, exist_ok=True)

        if config.video.source == VideoSource.photos:
            if photo_clips:
                clips = photo_clips
            else:
                # Fallback: Pexels/Pixabay keys not set or returned no photos
                log.warning("Photo search returned nothing — falling back to Pexels video clips")
                await manager.send_progress(project_id, {
                    "type": "progress", "task_type": "video", "progress": 20,
                    "msg": "Sin fotos disponibles — usando clips de video de Pexels",
                })
                pexels_urls = await fetch_pexels_clips(pexels_query, 6)
                clips = await _download_clips(pexels_urls, dl_dir, "pexels_fallback")
        else:
            # mixed_photos: always fetch pexels (fills in if photos also failed)
            pexels_urls = await fetch_pexels_clips(pexels_query, 3)
            pexels_clips = await _download_clips(pexels_urls, dl_dir, "pexels_mix")
            # Interleave: photo, video, photo, video, ...
            # If photo_clips is empty (no API key), result is pexels-only — still usable
            for i in range(max(len(photo_clips), len(pexels_clips))):
                if i < len(photo_clips):
                    clips.append(photo_clips[i])
                if i < len(pexels_clips):
                    clips.append(pexels_clips[i])

    # ── Local clips ────────────────────────────────────────────────
    if config.video.source in (VideoSource.local, VideoSource.mixed):
        clips_dir = Path(config.video.local_folder or LOCAL_CLIPS_DIR)
        if clips_dir.exists():
            clips = list(clips_dir.glob("*.mp4"))[:8]

    # ── Pexels clips ───────────────────────────────────────────────
    if config.video.source in (VideoSource.pexels, VideoSource.mixed) and len(clips) < 8:
        pexels_query = " ".join(config.topic.split()[:4])
        pexels_urls = await fetch_pexels_clips(pexels_query, 8 - len(clips))
        dl_dir = Path("projects") / project_id / "video" / "downloads"
        clips.extend(await _download_clips(pexels_urls, dl_dir, "pexels"))

    if not clips:
        project_service.update_layer_status(project_id, "video", LayerStatus.error, {
            "error": "Sin clips disponibles. Configura PEXELS_API_KEY o PIXABAY_API_KEY en Railway.",
        })
        return None

    list_file = Path("projects") / project_id / "video" / "clips.txt"
    list_file.write_text("\n".join(f"file '{c.resolve()}'" for c in clips))

    w, h = (1080, 1920) if aspect == "9:16" else (1920, 1080)
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}",
        "-c:v", "libx264", "-crf", "23",
        "-an",
        str(output_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    project_service.update_layer_status(project_id, "video", LayerStatus.ready, {
        "clips": len(clips),
        "source": config.video.source,
        "file": str(output_path),
    })
    return output_path


async def _assemble_from_user_clips(project_id: str, config: ProjectConfig,
                                    clips: list) -> Path:
    """Ensambla la capa de video solo con los clips subidos por el usuario,
    cortados por escena según la duración del audio (rotando los clips en orden)."""
    project_service.update_layer_status(project_id, "video", LayerStatus.pending)
    output_path = project_service.get_layer_path(project_id, "video")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    w, h = (1080, 1920) if config.aspect == "9:16" else (1920, 1080)
    vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"

    # Duracion del audio para repartir los clips a lo largo de toda la narracion.
    audio_path = project_service.get_layer_path(project_id, "audio")
    audio_dur = _audio_duration(audio_path) if audio_path.exists() else 0.0

    norm_dir = Path("projects") / project_id / "video" / "normalized"
    norm_dir.mkdir(parents=True, exist_ok=True)
    # Limpiar normalizados de corridas anteriores.
    for old in norm_dir.glob("seg_*.mp4"):
        old.unlink(missing_ok=True)

    norm_clips = []

    if audio_dur > 0:
        # CORTE POR ESCENA: dividir el audio en segmentos y tomar de cada clip
        # (rotando en orden) el trozo que cubre ese segmento.
        target_seg = 4.0
        n_segments = max(len(clips), int(round(audio_dur / target_seg)))
        seg_dur = audio_dur / n_segments
        for i in range(n_segments):
            src = clips[i % len(clips)]
            seg = norm_dir / f"seg_{i:03d}.mp4"
            # Tomar desde el inicio del clip; si el clip es mas corto que seg_dur,
            # ffmpeg usara lo que haya (se vera completo y se pasa al siguiente).
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(src),
                "-t", f"{seg_dur:.3f}",
                "-vf", vf,
                "-r", "30",
                "-c:v", "libx264", "-crf", "23", "-preset", "fast",
                "-pix_fmt", "yuv420p",
                "-an",
                str(seg),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            if seg.exists() and seg.stat().st_size > 0:
                norm_clips.append(seg)
    else:
        # Sin audio: normalizar cada clip completo (comportamiento simple).
        for i, c in enumerate(clips):
            seg = norm_dir / f"seg_{i:03d}.mp4"
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", str(c),
                "-vf", vf,
                "-r", "30",
                "-c:v", "libx264", "-crf", "23", "-preset", "fast",
                "-pix_fmt", "yuv420p",
                "-an",
                str(seg),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            if seg.exists() and seg.stat().st_size > 0:
                norm_clips.append(seg)

    if not norm_clips:
        project_service.update_layer_status(project_id, "video", LayerStatus.error, {
            "error": "Clip normalization failed",
        })
        return None

    list_file = Path("projects") / project_id / "video" / "clips.txt"
    list_file.write_text("\n".join(f"file '{c.resolve()}'" for c in norm_clips))

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        "-movflags", "+faststart",
        str(output_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    project_service.update_layer_status(project_id, "video", LayerStatus.ready, {
        "clips": len(norm_clips),
        "source": "user_clips",
        "file": str(output_path),
    })
    return output_path


async def _fetch_one_clip(query: str, dest_dir: Path, duration: float, aspect: str,
                          use_photos: bool) -> Path:
    """Devuelve UN clip para `query` (foto Ken Burns o clip de Pexels). None si nada
    o si falla (para no tumbar el ensamblaje: el caller cae a sus fallbacks)."""
    try:
        if use_photos:
            clips = await photo_sources.fetch_photo_clips(
                query=query, dest_dir=dest_dir, count=1, duration=duration, aspect=aspect,
            )
            return clips[0] if clips else None
        # Fuente de video (Pexels): pedimos varias y tomamos la 1ª que descargue bien
        urls = await fetch_pexels_clips(query, count=3)
        downloaded = await _download_clips(urls, dest_dir, "pex")
        return downloaded[0] if downloaded else None
    except Exception as e:
        log.warning(f"_fetch_one_clip falló para {query!r}: {e}")
        return None


async def assemble_video_layer_ab(project_id: str, config: ProjectConfig) -> Path:
    """Ensambla el video siguiendo el guion: segmenta en escenas y por cada una
    baja 2 visuales (A=visual_1, B=visual_2) según lo que se narra ahí. Concatena
    A,B,A,B… en orden para que la imagen pegue con la narración (encaje imagen-guion).
    """
    project_service.update_layer_status(project_id, "video", LayerStatus.pending)
    output_path = project_service.get_layer_path(project_id, "video")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    aspect = config.aspect
    use_photos = config.video.source in (VideoSource.photos, VideoSource.mixed_photos)
    scene_count = max(int(config.video.scene_count or 6), 1)

    # 1) Plan de escenas guiado por el guion. Si DeepSeek/parsing falla, marcar
    #    error (en BackgroundTask la excepción se perdería y la capa quedaría pending).
    try:
        scenes = await generate_scene_plan(project_id, config, scene_count)
    except Exception as e:
        log.error(f"scene-plan falló [{project_id}]: {e}", exc_info=True)
        project_service.update_layer_status(project_id, "video", LayerStatus.error, {
            "error": f"No se pudo generar el plan de escenas: {str(e)[:200]}",
        })
        return None
    if not scenes:
        project_service.update_layer_status(project_id, "video", LayerStatus.error, {
            "error": "No se pudo segmentar el guion en escenas.",
        })
        return None

    # 2) Duración por clip: reparte el audio entre todos los slots (2 por escena)
    total_slots = len(scenes) * 2
    clip_dur = float(config.video.clip_duration or 4)
    audio_path = project_service.get_layer_path(project_id, "audio")
    if audio_path.exists() and total_slots > 0:
        audio_dur = await _get_audio_duration(audio_path)
        if audio_dur > 0:
            clip_dur = round(audio_dur / total_slots, 2)

    # 3) Por cada escena, bajar visual_1 (A) y visual_2 (B) en orden
    ab_dir = Path("projects") / project_id / "video" / "ab"
    clips: list[Path] = []
    for i, scene in enumerate(scenes):
        for slot, key in (("a", "visual_1"), ("b", "visual_2")):
            query = scene.get(key) or scene.get("visual_1") or config.topic
            slot_dir = ab_dir / f"s{i:02d}_{slot}"
            clip = await _fetch_one_clip(query, slot_dir, clip_dur, aspect, use_photos)
            # Fallbacks: keyword del tema → reusar el último clip que sí salió
            if clip is None:
                clip = await _fetch_one_clip(config.topic, slot_dir, clip_dur, aspect, use_photos)
            if clip is None and clips:
                clip = clips[-1]
            if clip is not None:
                clips.append(clip)

    if not clips:
        project_service.update_layer_status(project_id, "video", LayerStatus.error, {
            "error": "Sin clips disponibles para el A/B split. Configura PEXELS_API_KEY o PIXABAY_API_KEY.",
        })
        return None

    # 4) Concatenar en orden y normalizar a la resolución del aspecto
    list_file = ab_dir / "clips.txt"
    list_file.parent.mkdir(parents=True, exist_ok=True)
    list_file.write_text("\n".join(f"file '{c.resolve()}'" for c in clips))

    w, h = (1080, 1920) if aspect == "9:16" else (1920, 1080)
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}",
        "-c:v", "libx264", "-crf", "23",
        "-an",
        str(output_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    project_service.update_layer_status(project_id, "video", LayerStatus.ready, {
        "clips": len(clips),
        "source": config.video.source,
        "ab_split": True,
        "scene_count": len(scenes),
        # Resumen para auditar el encaje imagen-narración desde la UI/API
        "scene_lines": [s.get("line", "")[:80] for s in scenes],
        "scene_visuals": [{"a": s.get("visual_1", ""), "b": s.get("visual_2", "")} for s in scenes],
        "file": str(output_path),
    })
    return output_path