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


async def generate_subtitles(project_id: str) -> Path:
    project_service.update_layer_status(project_id, "subtitles", LayerStatus.pending)
    output_path = project_service.get_layer_path(project_id, "subtitles")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    proc = await asyncio.create_subprocess_exec(
        "edge-tts",
        "--voice", "es-ES-AlvaroNeural",
        "--text", "placeholder",
        "--write-subtitles", str(output_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    project_service.update_layer_status(project_id, "subtitles", LayerStatus.ready, {
        "file": str(output_path),
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

    list_file = Path("projects") / project_id / "video" / "clips.txt"
    list_file.write_text("\n".join(f"file '{c.resolve()}'" for c in clips))

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
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