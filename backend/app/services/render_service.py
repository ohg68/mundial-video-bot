import asyncio
from pathlib import Path
from app.services import project_service
from app.models.project import LayerStatus

async def render_final(project_id: str) -> Path:
    meta = project_service.get_project(project_id)
    if not meta:
        raise ValueError(f"Project {project_id} not found")

    config = meta["config"]
    project_dir = Path("projects") / project_id
    output_path = project_dir / "output" / "final.mp4"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    video_path  = project_service.get_layer_path(project_id, "video")
    audio_path  = project_service.get_layer_path(project_id, "audio")
    music_path  = project_service.get_layer_path(project_id, "music")
    sub_path    = project_service.get_layer_path(project_id, "subtitles")
    overlay_path = project_service.get_layer_path(project_id, "overlay")

    has_video    = video_path.exists()
    has_audio    = audio_path.exists()
    has_music    = music_path.exists()
    has_subs     = sub_path.exists()
    has_overlay  = overlay_path.exists()

    if not has_video:
        raise ValueError("Video layer missing — cannot render")

    vol_narr = config.get("audio", {}).get("volume", 0.9)
    vol_music = config.get("music", {}).get("volume", 0.25)
    fade_out  = config.get("music", {}).get("fade_out", 3)

    inputs = ["-i", str(video_path)]
    filter_parts = []
    audio_inputs = 0

    if has_audio:
        inputs += ["-i", str(audio_path)]
        audio_inputs += 1
        filter_parts.append(f"[{audio_inputs}:a]volume={vol_narr}[narr]")

    if has_music:
        inputs += ["-i", str(music_path)]
        audio_inputs += 1
        filter_parts.append(
            f"[{audio_inputs}:a]volume={vol_music},afade=t=out:st=85:d={fade_out}[music]"
        )

    if has_audio and has_music:
        filter_parts.append("[narr][music]amix=inputs=2:duration=first[aout]")
        audio_map = "[aout]"
    elif has_audio:
        audio_map = "[narr]"
    elif has_music:
        audio_map = "[music]"
    else:
        audio_map = None

    vf_filters = []
    if has_subs:
        vf_filters.append(
            f"subtitles={sub_path}:force_style='"
            f"FontName={config.get('subtitles',{}).get('font','Arial')},"
            f"FontSize={config.get('subtitles',{}).get('font_size',48)},"
            f"PrimaryColour=&H00FFFFFF,"
            f"OutlineColour=&H00000000,"
            f"Outline=2,"
            f"Alignment=2'"
        )
    if has_overlay:
        vf_filters.append(
            f"movie={overlay_path}[logo];[in][logo]overlay=W-w-20:20:alpha=1[out]"
        )

    cmd = ["ffmpeg", "-y"] + inputs

    if vf_filters:
        cmd += ["-vf", ",".join(vf_filters)]

    if filter_parts:
        cmd += ["-filter_complex", ";".join(filter_parts)]

    if audio_map:
        cmd += ["-map", "0:v", "-map", audio_map]

    cmd += [
        "-c:v", "libx264", "-crf", "22", "-preset", "fast",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(output_path),
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg error: {stderr.decode()}")

    size_mb = round(output_path.stat().st_size / 1024 / 1024, 1)
    meta["output"] = str(output_path)
    project_service.update_layer_status(project_id, "video", LayerStatus.ready, {
        "output": str(output_path),
        "size_mb": size_mb,
    })
    return output_path
