import asyncio
import logging
import subprocess
from pathlib import Path
from app.services import project_service
from app.models.project import LayerStatus

log = logging.getLogger(__name__)

_has_subtitles_filter = None


def _check_subtitles_filter() -> bool:
    global _has_subtitles_filter
    if _has_subtitles_filter is None:
        r = subprocess.run(
            ["ffmpeg", "-filters"], capture_output=True, text=True,
        )
        _has_subtitles_filter = "subtitles" in r.stdout
    return _has_subtitles_filter


def _escape_srt_path(p: Path) -> str:
    s = str(p.resolve().as_posix())
    s = s.replace("\\", "\\\\")
    s = s.replace(":", "\\:")
    s = s.replace("'", "\\'")
    return s


async def render_final(project_id: str) -> Path:
    meta = project_service.get_project(project_id)
    if not meta:
        raise ValueError(f"Project {project_id} not found")

    config = meta["config"]
    project_dir = Path("projects") / project_id
    output_path = project_dir / "output" / "final.mp4"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    video_path   = project_service.get_layer_path(project_id, "video")
    audio_path   = project_service.get_layer_path(project_id, "audio")
    music_path   = project_service.get_layer_path(project_id, "music")
    sub_path     = project_service.get_layer_path(project_id, "subtitles")
    overlay_path = project_service.get_layer_path(project_id, "overlay")

    has_video   = video_path.exists()
    has_audio   = audio_path.exists()
    has_music   = music_path.exists()
    has_subs    = sub_path.exists()
    has_overlay = overlay_path.exists()

    if not has_video:
        raise ValueError("Video layer missing — cannot render")

    vol_narr  = config.get("audio", {}).get("volume", 0.9)
    vol_music = config.get("music", {}).get("volume", 0.25)
    fade_out  = config.get("music", {}).get("fade_out", 3)

    inputs = ["-i", str(video_path)]
    next_idx = 1
    filter_parts = []

    # ---- Audio chain ----
    if has_audio:
        inputs += ["-i", str(audio_path)]
        filter_parts.append(f"[{next_idx}:a]volume={vol_narr}[narr]")
        next_idx += 1

    if has_music:
        inputs += ["-i", str(music_path)]
        filter_parts.append(
            f"[{next_idx}:a]volume={vol_music},afade=t=out:st=85:d={fade_out}[music]"
        )
        next_idx += 1

    if has_audio and has_music:
        filter_parts.append("[narr][music]amix=inputs=2:duration=first[aout]")
        audio_map = "[aout]"
    elif has_audio:
        audio_map = "[narr]"
    elif has_music:
        audio_map = "[music]"
    else:
        audio_map = None

    # ---- Video chain: [0:v] → [vsub] → [vout] inside filter_complex ----
    overlay_idx = None
    if has_overlay:
        inputs += ["-i", str(overlay_path)]
        overlay_idx = next_idx
        next_idx += 1

    video_label = "[0:v]"

    if has_subs and _check_subtitles_filter():
        srt_esc = _escape_srt_path(sub_path)
        sub_cfg = config.get("subtitles", {})
        filter_parts.append(
            f"{video_label}subtitles={srt_esc}:force_style='"
            f"FontName={sub_cfg.get('font', 'Arial')},"
            f"FontSize={sub_cfg.get('font_size', 48)},"
            f"PrimaryColour=&H00FFFFFF,"
            f"OutlineColour=&H00000000,"
            f"Outline=2,"
            f"Alignment=2'[vsub]"
        )
        video_label = "[vsub]"
    elif has_subs:
        log.warning("Subtitles skipped — FFmpeg lacks libass (subtitles filter)")

    if has_overlay:
        filter_parts.append(
            f"{video_label}[{overlay_idx}:v]overlay=W-w-20:20[vout]"
        )
        video_label = "[vout]"

    video_map = video_label if video_label != "[0:v]" else "0:v"

    cmd = ["ffmpeg", "-y"] + inputs

    if filter_parts:
        cmd += ["-filter_complex", ";".join(filter_parts)]

    cmd += ["-map", video_map]
    if audio_map:
        cmd += ["-map", audio_map]

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
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg error: {stderr.decode()}")

    size_mb = round(output_path.stat().st_size / 1024 / 1024, 1)
    meta["output"] = str(output_path)
    project_service.update_layer_status(project_id, "video", LayerStatus.ready, {
        "output": str(output_path),
        "size_mb": size_mb,
    })
    return output_path
