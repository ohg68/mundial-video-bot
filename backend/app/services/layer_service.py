import os
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

    if config.audio.custom_file:
        import shutil
        shutil.copy2(config.audio.custom_file, output_path)
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

    # edge-tts genera subtítulos SRT sincronizados; requiere --write-media (descartable)
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


async def _fetch_one_clip(query: str, dest_dir: Path, duration: float, aspect: str,
                          use_photos: bool) -> Path:
    """Devuelve UN clip para `query` (foto Ken Burns o clip de Pexels). None si nada."""
    if use_photos:
        clips = await fetch_photo_clips(
            query=query, dest_dir=dest_dir, count=1, duration=duration, aspect=aspect,
        )
        return clips[0] if clips else None
    # Fuente de video (Pexels): pedimos varias y tomamos la 1ª que descargue bien
    urls = await fetch_pexels_clips(query, count=3)
    downloaded = await _download_clips(urls, dest_dir, "pex")
    return downloaded[0] if downloaded else None


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