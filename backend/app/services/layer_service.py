import os
import httpx
import asyncio
import subprocess
from pathlib import Path
from app.models.project import ProjectConfig, VideoSource, LayerStatus
from app.services import project_service

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

    meta = project_service.get_project(project_id)
    meta["config"]["script"] = script
    project_dir = Path("projects") / project_id
    (project_dir / "project.json").write_text(
        __import__("json").dumps(meta, indent=2, ensure_ascii=False)
    )
    return script


def _edge_rate(speed: float) -> str:
    """Convierte un multiplicador de velocidad (1.0 = normal) al formato +N%/-N% de edge-tts."""
    pct = int(round((speed - 1) * 100))
    return f"+{pct}%" if pct >= 0 else f"{pct}%"


async def generate_audio(project_id: str, config: ProjectConfig) -> Path:
    project_service.update_layer_status(project_id, "audio", LayerStatus.pending)
    script = config.script or await generate_script(project_id, config)
    output_path = project_service.get_layer_path(project_id, "audio")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    voice = config.audio.voice.value if config.audio.custom_file is None else None

    if config.audio.custom_file:
        # Audio propio: copiar tal cual. Los subtítulos no se pueden sincronizar
        # automáticamente con un audio externo (no hay timings de edge-tts).
        import shutil
        shutil.copy2(config.audio.custom_file, output_path)
    else:
        rate = _edge_rate(config.audio.speed)
        # Generar AUDIO y SUBTÍTULOS en la MISMA llamada a edge-tts: así los
        # timings del .vtt corresponden exactamente a la narración sintetizada.
        # (Antes los subtítulos se generaban aparte con "placeholder" y no
        # coincidían con el audio.)
        sub_path = project_service.get_layer_path(project_id, "subtitles")
        sub_path.parent.mkdir(parents=True, exist_ok=True)

        tmp_path = output_path.with_suffix(".tmp.mp3")
        proc = await asyncio.create_subprocess_exec(
            "edge-tts",
            "--voice", voice,
            "--rate", rate,
            "--text", script,
            "--write-media", str(tmp_path),
            "--write-subtitles", str(sub_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            tmp_path.unlink(missing_ok=True)
            raise RuntimeError(f"edge-tts error: {stderr.decode()[-1000:]}")

        # Convertir a mp3 válido con ffmpeg (edge-tts escribe un contenedor que
        # algunos reproductores/filtros no leen bien sin re-encode).
        conv = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", str(tmp_path),
            "-acodec", "libmp3lame", "-q:a", "2",
            str(output_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await conv.communicate()
        tmp_path.unlink(missing_ok=True)

        # Marcar la capa de subtítulos como lista (se generó junto al audio).
        if sub_path.exists() and sub_path.stat().st_size > 0:
            project_service.update_layer_status(project_id, "subtitles", LayerStatus.ready, {
                "file": str(sub_path),
                "synced_with": "audio",
            })

    project_service.update_layer_status(project_id, "audio", LayerStatus.ready, {
        "voice": voice or "custom",
        "file": str(output_path),
    })
    return output_path


async def generate_subtitles(project_id: str, config: ProjectConfig = None) -> Path:
    """Genera subtítulos sincronizados con la narración.

    Los subtítulos de edge-tts solo coinciden con el audio si se sintetizan con
    el MISMO texto, voz y velocidad. Por eso, para voces edge-tts, lo correcto
    es generarlos junto al audio (ver generate_audio). Esta función:

      - Si los subtítulos ya existen (generados con el audio): los devuelve.
      - Si no existen pero hay guion y voz edge-tts: los regenera con el guion real.
      - Si el audio es un archivo propio del usuario: no puede sincronizar
        automáticamente y deja la capa marcada como no disponible.
    """
    output_path = project_service.get_layer_path(project_id, "subtitles")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Si ya se generaron junto al audio, no hay nada que hacer.
    if output_path.exists() and output_path.stat().st_size > 0:
        project_service.update_layer_status(project_id, "subtitles", LayerStatus.ready, {
            "file": str(output_path),
            "synced_with": "audio",
        })
        return output_path

    project_service.update_layer_status(project_id, "subtitles", LayerStatus.pending)

    # Recuperar config si no se pasó.
    if config is None:
        meta = project_service.get_project(project_id)
        cfg = meta.get("config", {}) if meta else {}
        script = cfg.get("script")
        audio_cfg = cfg.get("audio", {})
        voice = audio_cfg.get("voice")
        speed = audio_cfg.get("speed", 1.0)
        custom_file = audio_cfg.get("custom_file")
    else:
        script = config.script
        voice = config.audio.voice.value if config.audio.custom_file is None else None
        speed = config.audio.speed
        custom_file = config.audio.custom_file

    # Audio propio: no hay forma de derivar timings automáticamente.
    if custom_file or not voice:
        project_service.update_layer_status(project_id, "subtitles", LayerStatus.error, {
            "error": "No se pueden generar subtítulos sincronizados para audio personalizado. "
                     "Sube un archivo de subtítulos manualmente.",
        })
        return None

    if not script:
        project_service.update_layer_status(project_id, "subtitles", LayerStatus.error, {
            "error": "No hay guion disponible para generar subtítulos.",
        })
        return None

    rate = _edge_rate(speed)
    # Regenerar con el GUION REAL (no 'placeholder') y la misma voz/velocidad.
    proc = await asyncio.create_subprocess_exec(
        "edge-tts",
        "--voice", voice,
        "--rate", rate,
        "--text", script,
        "--write-subtitles", str(output_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        project_service.update_layer_status(project_id, "subtitles", LayerStatus.error, {
            "error": f"edge-tts error: {stderr.decode()[-500:]}",
        })
        return None

    project_service.update_layer_status(project_id, "subtitles", LayerStatus.ready, {
        "file": str(output_path),
        "synced_with": "regenerated",
    })
    return output_path


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
        # Use only first 4 words of topic for Pexels search
        pexels_query = " ".join(config.topic.split()[:4])
        pexels_urls = await fetch_pexels_clips(pexels_query, 8 - len(clips))
        dl_dir = Path("projects") / project_id / "video" / "downloads"
        dl_dir.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient() as client:
            for i, url in enumerate(pexels_urls):
                dest = dl_dir / f"pexels_{i}.mp4"
                r = await client.get(url, timeout=30, follow_redirects=True)
                dest.write_bytes(r.content)
                clips.append(dest)

    if not clips:
        project_service.update_layer_status(project_id, "video", LayerStatus.error, {
            "error": "No clips found"
        })
        return None

    # Normalizar CADA clip a 1080x1920 por separado antes de concatenar.
    # El concat demuxer asume codec/timebase/resolución idénticos; los clips de
    # Pexels vienen en tamaños distintos, así que normalizarlos uno a uno evita
    # saltos y fallos silenciosos del concat directo.
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

    # Ahora el concat es lossless: todos los clips comparten formato.
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