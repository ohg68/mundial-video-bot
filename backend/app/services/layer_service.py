import os
import logging
import httpx
import asyncio
import subprocess
from pathlib import Path
from app.models.project import ProjectConfig, VideoSource, LayerStatus
from app.services import project_service, photo_sources

log = logging.getLogger(__name__)
LOCAL_CLIPS_DIR = Path(os.getenv("LOCAL_CLIPS_DIR", "clips"))


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

    # Persistir en SQLite (project.json es efímero en Railway y nadie lo lee)
    project_service.update_project_config(project_id, {"script": script})
    return script


async def generate_audio(project_id: str, config: ProjectConfig) -> Path:
    project_service.update_layer_status(project_id, "audio", LayerStatus.pending)
    script = config.script or await generate_script(project_id, config)
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