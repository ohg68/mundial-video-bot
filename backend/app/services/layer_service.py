import os
import json
import httpx
import asyncio
import subprocess
from pathlib import Path
from app.models.project import ProjectConfig, VideoSource, LayerStatus
from app.services import project_service

LOCAL_CLIPS_DIR = Path(os.getenv("LOCAL_CLIPS_DIR", "clips"))

# Voz de Google TTS. WaveNet masculina en español (locutor). Gratis hasta
# 1 millón de caracteres/mes. Cambiable con la variable ELEVEN... perdón,
# con la variable GOOGLE_TTS_VOICE.
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
    prompt = f"""Eres un locutor deportivo para un canal de YouTube del Mundial 2026.
Genera un guion en espanol para un video corto (90 segundos) sobre:
Tema: {config.topic}
Partido: {config.match or "Mundial 2026"}
Fecha: {config.match_date or ""}

El guion debe:
- Tener un gancho en los primeros 5 segundos
- Ser informativo y emocionante
- Terminar con una llamada a la accion (suscribete, comenta)
- Durar aproximadamente 90 segundos al leerlo
- Solo el texto que leera el locutor, sin indicaciones de escena

Responde SOLO con el guion, sin introduccion ni explicacion."""

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

    meta = project_service.get_project(project_id)
    meta["config"]["script"] = script
    project_dir = Path("projects") / project_id
    (project_dir / "project.json").write_text(
        __import__("json").dumps(meta, indent=2, ensure_ascii=False)
    )
    return script


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


async def generate_audio(project_id: str, config: ProjectConfig) -> Path:
    """Genera la narracion con Google TTS y subtitulos repartidos por duracion."""
    project_service.update_layer_status(project_id, "audio", LayerStatus.pending)
    script = config.script or await generate_script(project_id, config)
    output_path = project_service.get_layer_path(project_id, "audio")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if config.audio.custom_file:
        import shutil
        shutil.copy2(config.audio.custom_file, output_path)
        project_service.update_layer_status(project_id, "audio", LayerStatus.ready, {
            "voice": "custom",
            "file": str(output_path),
        })
        return output_path

    _ensure_google_credentials()
    voice_name = getattr(config.audio, "voice_name", None) or GOOGLE_TTS_VOICE

    # Velocidad: config.audio.speed (1.0 normal). Google usa speaking_rate.
    speaking_rate = float(getattr(config.audio, "speed", 1.0) or 1.0)
    speaking_rate = max(0.25, min(4.0, speaking_rate))

    def _synthesize():
        from google.cloud import texttospeech
        client = texttospeech.TextToSpeechClient()
        synthesis_input = texttospeech.SynthesisInput(text=script)
        voice = texttospeech.VoiceSelectionParams(
            language_code=GOOGLE_TTS_LANG,
            name=voice_name,
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=speaking_rate,
        )
        resp = client.synthesize_speech(
            input=synthesis_input, voice=voice, audio_config=audio_config
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
    tmp_mp3 = output_path.with_suffix(".raw.mp3")
    tmp_mp3.write_bytes(audio_bytes)
    conv = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-i", str(tmp_mp3),
        "-acodec", "libmp3lame", "-q:a", "2",
        str(output_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await conv.communicate()
    tmp_mp3.unlink(missing_ok=True)

    # Subtitulos repartidos dentro del rango real de voz del audio generado.
    dur = _audio_duration(output_path)
    if dur > 0:
        v_start, v_end = _voice_range(output_path, dur)
        srt = _build_srt_by_duration(script, dur, voice_start=v_start, voice_end=v_end)
        sub_path = project_service.get_layer_path(project_id, "subtitles")
        sub_path.parent.mkdir(parents=True, exist_ok=True)
        if sub_path.suffix.lower() != ".srt":
            sub_path = sub_path.with_suffix(".srt")
        sub_path.write_text(srt, encoding="utf-8")
        project_service.update_layer_status(project_id, "subtitles", LayerStatus.ready, {
            "file": str(sub_path),
            "synced_with": "audio",
        })

    project_service.update_layer_status(project_id, "audio", LayerStatus.ready, {
        "voice": voice_name,
        "file": str(output_path),
    })
    return output_path

async def generate_subtitles(project_id: str, config: ProjectConfig = None) -> Path:
    """Devuelve los subtitulos generados junto al audio."""
    sub_path = project_service.get_layer_path(project_id, "subtitles")
    candidates = [sub_path, sub_path.with_suffix(".srt"), sub_path.with_suffix(".vtt")]
    for c in candidates:
        if c.exists() and c.stat().st_size > 0:
            project_service.update_layer_status(project_id, "subtitles", LayerStatus.ready, {
                "file": str(c),
                "synced_with": "audio",
            })
            return c

    project_service.update_layer_status(project_id, "subtitles", LayerStatus.error, {
        "error": "Los subtitulos se generan junto con el audio. Genera primero la capa de audio.",
    })
    return None


async def generate_scene_queries(script: str, n_scenes: int = 6) -> list:
    """Usa DeepSeek para convertir el guion en términos de búsqueda visuales.

    Divide el guion en escenas y, por cada una, devuelve 2-3 palabras en inglés
    que describen qué clip mostrar (Pexels busca mejor en inglés). Así cada
    parte del vídeo muestra algo relacionado con lo que se está narrando.

    Devuelve una lista de strings, p.ej.: ["soccer player passing ball",
    "crowded football stadium", "fast winger sprinting"].
    """
    deepseek_key = os.getenv("DEEPSEEK_API_KEY")
    prompt = f"""Eres un editor de video. Te doy el guion de un video corto sobre futbol.
Divide el contenido en exactamente {n_scenes} escenas visuales y, para CADA escena,
dame 2 o 3 palabras EN INGLES que sirvan para buscar un clip de stock que ilustre
esa parte. Usa terminos visuales y genericos de futbol (no nombres propios, porque
no hay clips de jugadores concretos en bancos de stock).

Ejemplos de buenas busquedas: "soccer player passing", "football stadium crowd",
"goalkeeper saving goal", "soccer ball close up", "fast winger running",
"football tactics board", "soccer fans celebrating".

Guion:
{script}

Responde SOLO con un JSON array de {n_scenes} strings, sin explicacion.
Ejemplo de formato: ["soccer player passing", "stadium crowd cheering", "..."]"""

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.deepseek.com/chat/completions",
                headers={
                    "Authorization": f"Bearer {deepseek_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "deepseek-chat",
                    "max_tokens": 400,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=30,
            )
        data = resp.json()
        content = data["choices"][0]["message"]["content"].strip()
        # Limpiar posibles ``` o ```json alrededor del JSON.
        content = content.replace("```json", "").replace("```", "").strip()
        import json as _json
        queries = _json.loads(content)
        # Validar que sea una lista de strings no vacíos.
        queries = [q.strip() for q in queries if isinstance(q, str) and q.strip()]
        if queries:
            return queries
    except Exception:
        pass

    # Fallback: si DeepSeek falla, usar términos genéricos de fútbol variados.
    return [
        "soccer match action",
        "football stadium crowd",
        "soccer player close up",
        "soccer ball field",
        "football fans celebrating",
        "soccer training",
    ]


async def fetch_pexels_clips(query: str, count: int = 8) -> list:
    pexels_key = os.getenv("PEXELS_API_KEY")
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
        for f in v.get("video_files", []):
            if f.get("quality") == "hd":
                urls.append(f["link"])
                break
    return urls


async def fetch_one_clip(query: str) -> str:
    """Busca en Pexels y devuelve la URL de UN clip para la query dada.

    Si no hay resultado vertical HD, intenta sin filtro de orientación.
    Devuelve None si no encuentra nada.
    """
    pexels_key = os.getenv("PEXELS_API_KEY")
    async with httpx.AsyncClient() as client:
        for orientation in ("portrait", None):
            params = {"query": query, "per_page": 5}
            if orientation:
                params["orientation"] = orientation
            try:
                resp = await client.get(
                    "https://api.pexels.com/videos/search",
                    headers={"Authorization": pexels_key},
                    params=params,
                    timeout=15,
                )
                data = resp.json()
            except Exception:
                continue
            for v in data.get("videos", []):
                for f in v.get("video_files", []):
                    if f.get("quality") == "hd":
                        return f["link"]
    return None


async def assemble_video_layer(project_id: str, config: ProjectConfig) -> Path:
    project_service.update_layer_status(project_id, "video", LayerStatus.pending)
    output_path = project_service.get_layer_path(project_id, "video")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    clips = []

    if config.video.source in (VideoSource.local, VideoSource.mixed):
        clips_dir = Path(config.video.local_folder or LOCAL_CLIPS_DIR)
        if clips_dir.exists():
            clips = list(clips_dir.glob("*.mp4"))[:8]

    if config.video.source in (VideoSource.pexels, VideoSource.mixed) and len(clips) < 8:
        # Selección por escena: DeepSeek convierte el guion en términos visuales
        # y bajamos un clip distinto por cada escena, así el vídeo pega con lo
        # que se está narrando (en vez de buscar las primeras 4 palabras del tema).
        script = config.script or ""
        scene_queries = await generate_scene_queries(script, n_scenes=8 - len(clips))

        dl_dir = Path("projects") / project_id / "video" / "downloads"
        dl_dir.mkdir(parents=True, exist_ok=True)
        used_urls = set()
        async with httpx.AsyncClient() as client:
            for i, query in enumerate(scene_queries):
                url = await fetch_one_clip(query)
                # Si ese término no dio clip, o ya se usó, probar un genérico.
                if not url or url in used_urls:
                    url = await fetch_one_clip("soccer match action")
                if not url or url in used_urls:
                    continue
                used_urls.add(url)
                dest = dl_dir / f"scene_{i}.mp4"
                try:
                    r = await client.get(url, timeout=30, follow_redirects=True)
                    dest.write_bytes(r.content)
                    clips.append(dest)
                except Exception:
                    continue

    if not clips:
        project_service.update_layer_status(project_id, "video", LayerStatus.error, {
            "error": "No clips found"
        })
        return None

    norm_dir = Path("projects") / project_id / "video" / "normalized"
    norm_dir.mkdir(parents=True, exist_ok=True)
    norm_clips = []
    for i, c in enumerate(clips):
        norm = norm_dir / f"norm_{i:02d}.mp4"
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", str(c),
            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,"
                   "crop=1080:1920,setsar=1",
            "-r", "30",
            "-c:v", "libx264", "-crf", "23", "-preset", "fast",
            "-pix_fmt", "yuv420p",
            "-an",
            str(norm),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        if norm.exists() and norm.stat().st_size > 0:
            norm_clips.append(norm)

    if not norm_clips:
        project_service.update_layer_status(project_id, "video", LayerStatus.error, {
            "error": "Clip normalization failed"
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
        "source": config.video.source,
        "file": str(output_path),
    })
    return output_path
