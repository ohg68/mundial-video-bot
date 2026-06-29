import asyncio
import logging
import os
import subprocess
from pathlib import Path
from app.services import project_service
from app.models.project import LayerStatus

log = logging.getLogger(__name__)

_has_subtitles_filter = None

# Limita renders FFmpeg simultáneos para evitar OOM en Railway.
# Configurable con MAX_CONCURRENT_RENDERS (default 2).
_render_semaphore = asyncio.Semaphore(int(os.getenv("MAX_CONCURRENT_RENDERS", "2")))


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


def _parse_srt(srt_text: str):
    """Parsea un SRT en bloques (start, end, text). Tiempos a 'H:MM:SS.cc' (ASS)."""
    import re
    blocks = []
    pattern = re.compile(
        r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
    )
    chunks = re.split(r"\n\s*\n", srt_text.strip())
    for chunk in chunks:
        m = pattern.search(chunk)
        if not m:
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2 = m.groups()
        start = f"{int(h1)}:{m1}:{s1}.{ms1[:2]}"
        end = f"{int(h2)}:{m2}:{s2}.{ms2[:2]}"
        lines = chunk.split("\n")
        # texto = todo lo que sigue a la línea del timestamp
        ts_idx = next((i for i, ln in enumerate(lines) if "-->" in ln), 0)
        text = "\\N".join(ln.strip() for ln in lines[ts_idx + 1:] if ln.strip())
        if text:
            blocks.append((start, end, text))
    return blocks


def _srt_to_ass(srt_path: Path, ass_path: Path, w: int, h: int, sub_cfg: dict):
    """Convierte SRT a ASS con resolución REAL en el header (FontSize en píxeles
    reales y predecibles) y estilo tipo caption: outline grueso, márgenes y wrap."""
    font = sub_cfg.get("font", "Arial")
    font_size = int(sub_cfg.get("font_size", 72))
    position = sub_cfg.get("position", "bottom")

    # Alignment ASS: 2=abajo-centro, 5=medio-centro, 8=arriba-centro
    alignment = {"bottom": 2, "center": 5, "top": 8}.get(position, 2)
    # Márgenes proporcionales al ancho/alto
    margin_lr = max(int(w * 0.07), 40)         # ~7% laterales → evita que se corte
    margin_v = max(int(h * 0.12), 80)          # separación del borde
    outline = max(int(font_size * 0.06), 3)    # contorno grueso para legibilidad

    blocks = _parse_srt(srt_path.read_text(encoding="utf-8", errors="ignore"))

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{font_size},&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,{outline},1,{alignment},{margin_lr},{margin_lr},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = "\n".join(
        f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}"
        for start, end, text in blocks
    )
    ass_path.write_text(header + events, encoding="utf-8")
    return ass_path


async def render_final(project_id: str, quality: str = "full") -> Path:
    meta = project_service.get_project(project_id)
    if not meta:
        raise ValueError(f"Project {project_id} not found")

    config = meta["config"]
    project_dir = Path("projects") / project_id
    output_name = "preview.mp4" if quality == "quick" else "final.mp4"
    output_path = project_dir / "output" / output_name
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
        sub_cfg = config.get("subtitles", {})
        # Resolución real del video → ASS con PlayRes correcto (FontSize en px reales)
        sub_w, sub_h = (1080, 1920) if config.get("aspect", "9:16") == "9:16" else (1920, 1080)
        ass_path = sub_path.with_suffix(".ass")
        _srt_to_ass(sub_path, ass_path, sub_w, sub_h, sub_cfg)
        ass_esc = _escape_srt_path(ass_path)
        filter_parts.append(f"{video_label}ass={ass_esc}[vsub]")
        video_label = "[vsub]"
    elif has_subs:
        log.warning("Subtitles skipped — FFmpeg lacks libass (subtitles filter)")

    if has_overlay:
        filter_parts.append(
            f"{video_label}[{overlay_idx}:v]overlay=W-w-20:20[vout]"
        )
        video_label = "[vout]"

    video_map = video_label if video_label != "[0:v]" else "0:v"

    # Quick render: scale to 540p. If the video is already in filter_complex we must
    # add the scale there; otherwise a simple -vf works.
    use_vf_scale = False
    if quality == "quick":
        if filter_parts and video_map.startswith("["):
            filter_parts.append(f"{video_map}scale=-2:540[qvout]")
            video_map = "[qvout]"
        else:
            use_vf_scale = True

    cmd = ["ffmpeg", "-y"] + inputs

    if filter_parts:
        cmd += ["-filter_complex", ";".join(filter_parts)]

    cmd += ["-map", video_map]
    if audio_map:
        cmd += ["-map", audio_map]

    cmd += [
        "-c:v", "libx264",
        "-crf", "32" if quality == "quick" else "22",
        "-preset", "ultrafast" if quality == "quick" else "fast",
        *(["-vf", "scale=-2:540"] if use_vf_scale else []),
        "-c:a", "aac", "-b:a", "96k" if quality == "quick" else "192k",
        "-shortest",
        str(output_path),
    ]

    # Semáforo: evita que varios renders saturen CPU/RAM en Railway
    async with _render_semaphore:
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
