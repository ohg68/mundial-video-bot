import asyncio
import json
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

# Estándar de redes sociales para loudness (YouTube/IG/TikTok/X).
LOUDNORM_I = -14.0
LOUDNORM_TP = -1.0
LOUDNORM_LRA = 11.0

# Transferencias HDR que requieren tone-mapping a SDR.
HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}  # PQ (HDR10) y HLG


def _check_subtitles_filter() -> bool:
    global _has_subtitles_filter
    if _has_subtitles_filter is None:
        r = subprocess.run(
            ["ffmpeg", "-filters"], capture_output=True, text=True,
        )
        _has_subtitles_filter = "subtitles" in r.stdout
    return _has_subtitles_filter


def _is_hdr(video: Path) -> bool:
    """True si el vídeo usa transferencia PQ o HLG (footage de móvil típico)."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=color_transfer",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
            capture_output=True, text=True, check=True,
        )
        return out.stdout.strip() in HDR_TRANSFERS
    except Exception:
        return False


def _measure_loudness(path: Path) -> dict | None:
    """Primera pasada de loudnorm; parsea la medición JSON. None si falla."""
    filt = f"loudnorm=I={LOUDNORM_I}:TP={LOUDNORM_TP}:LRA={LOUDNORM_LRA}:print_format=json"
    proc = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-nostats", "-i", str(path),
         "-af", filt, "-vn", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    err = proc.stderr
    a, b = err.rfind("{"), err.rfind("}")
    if a == -1 or b == -1 or b <= a:
        return None
    try:
        data = json.loads(err[a:b + 1])
    except json.JSONDecodeError:
        return None
    needed = {"input_i", "input_tp", "input_lra", "input_thresh", "target_offset"}
    return data if needed.issubset(data.keys()) else None


def _escape_srt_path(p: Path) -> str:
    s = str(p.resolve().as_posix())
    s = s.replace("\\", "\\\\")
    s = s.replace(":", "\\:")
    s = s.replace("'", "\\'")
    return s


def _fmt_ass_time(sec: float) -> str:
    """Segundos → 'H:MM:SS.cc' (formato de tiempo ASS), sin negativos."""
    sec = max(0.0, sec)
    h = int(sec // 3600); sec -= h * 3600
    m = int(sec // 60); sec -= m * 60
    s = int(sec)
    cs = int(round((sec - s) * 100))
    if cs == 100:
        s += 1; cs = 0
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _parse_srt(srt_text: str):
    """Parsea un SRT en bloques (start_sec, end_sec, text) con tiempos en segundos."""
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
        start = int(h1) * 3600 + int(m1) * 60 + int(s1) + int(ms1) / 1000
        end = int(h2) * 3600 + int(m2) * 60 + int(s2) + int(ms2) / 1000
        lines = chunk.split("\n")
        # texto = todo lo que sigue a la línea del timestamp
        ts_idx = next((i for i, ln in enumerate(lines) if "-->" in ln), 0)
        text = "\\N".join(ln.strip() for ln in lines[ts_idx + 1:] if ln.strip())
        if text:
            blocks.append((start, end, text))
    return blocks


def _ass_color(color: str) -> str:
    """Convierte un color (nombre o #RRGGBB) al formato ASS &H00BBGGRR (fill del texto)."""
    named = {
        "white": "&H00FFFFFF", "black": "&H00000000", "yellow": "&H0000FFFF",
        "red": "&H000000FF", "green": "&H0000FF00", "blue": "&H00FF0000",
        "cyan": "&H00FFFF00", "magenta": "&H00FF00FF", "pink": "&H00B469FF",
        "orange": "&H0000A5FF",
    }
    c = (color or "white").strip().lower()
    if c in named:
        return named[c]
    if c.startswith("#") and len(c) == 7:
        r, g, b = c[1:3], c[3:5], c[5:7]
        return f"&H00{b}{g}{r}".upper()
    return named["white"]


def _srt_to_ass(srt_path: Path, ass_path: Path, w: int, h: int, sub_cfg: dict):
    """Convierte SRT a ASS con resolución REAL en el header (FontSize en píxeles
    reales y predecibles) y estilo tipo caption: outline grueso, márgenes y wrap."""
    font = sub_cfg.get("font", "Arial")
    font_size = int(sub_cfg.get("font_size", 72))
    position = sub_cfg.get("position", "bottom")
    offset = float(sub_cfg.get("time_offset", 0.0) or 0.0)  # + atrasa, - adelanta
    primary = _ass_color(sub_cfg.get("color", "white"))     # color de relleno del texto

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
Style: Default,{font},{font_size},{primary},&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,{outline},1,{alignment},{margin_lr},{margin_lr},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = "\n".join(
        f"Dialogue: 0,{_fmt_ass_time(start + offset)},{_fmt_ass_time(end + offset)},Default,,0,0,0,,{text}"
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

    # ---- Video chain (todo en filter_complex): HDR → overlay → subtítulos ----
    overlay_idx = None
    if has_overlay:
        inputs += ["-i", str(overlay_path)]
        overlay_idx = next_idx
        next_idx += 1

    video_label = "[0:v]"

    # Tone-mapping HDR->SDR si el video layer trae transferencia HLG/PQ
    # (relevante si entran clips locales de móvil; Pexels suele ser SDR).
    if _is_hdr(video_path):
        filter_parts.append(
            f"{video_label}zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
            f"tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,"
            f"format=yuv420p[vtm]"
        )
        video_label = "[vtm]"

    # Overlay (logo arriba-derecha) ANTES de subtítulos: si no, puede taparlos.
    if has_overlay:
        filter_parts.append(
            f"{video_label}[{overlay_idx}:v]overlay=W-w-20:20[vov]"
        )
        video_label = "[vov]"

    # Subtítulos SIEMPRE el último filtro de vídeo.
    # Kinéticos (WebM alpha de HyperFrames) tienen prioridad sobre los ASS clásicos.
    captions_path = project_dir / "motion" / "captions.webm"
    has_kinetic = captions_path.exists() and captions_path.stat().st_size > 0

    if has_kinetic:
        sub_w, sub_h = (1080, 1920) if config.get("aspect", "9:16") == "9:16" else (1920, 1080)
        # libvpx-vp9 como decoder de entrada: el decoder nativo ignora el canal alpha
        inputs += ["-c:v", "libvpx-vp9", "-i", str(captions_path)]
        cap_idx = next_idx
        next_idx += 1
        filter_parts.append(
            f"[{cap_idx}:v]scale={sub_w}:{sub_h}[kincap];"
            f"{video_label}[kincap]overlay=0:0:eof_action=pass[vkin]"
        )
        video_label = "[vkin]"
    elif has_subs and _check_subtitles_filter():
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

    # ---- Compose (a archivo intermedio si luego hay loudnorm) --------------
    do_loudnorm = quality != "quick" and audio_map is not None
    compose_out = output_path.with_suffix(".prenorm.mp4") if do_loudnorm else output_path

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
        "-pix_fmt", "yuv420p",
        *(["-vf", "scale=-2:540"] if use_vf_scale else []),
        "-c:a", "aac", "-b:a", "96k" if quality == "quick" else "192k",
        "-ar", "48000",
        "-shortest", "-movflags", "+faststart",
        str(compose_out),
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
        if do_loudnorm:
            compose_out.unlink(missing_ok=True)
        raise RuntimeError(f"FFmpeg error (compose): {stderr.decode()[-2000:]}")

    # ---- Loudness two-pass -> final ---------------------------------------
    # Estandariza el volumen de la mezcla narración+música a -14 LUFS.
    # Solo en render full con audio; el preview rápido se salta este paso.
    if do_loudnorm:
        prenorm = compose_out
        measurement = await asyncio.get_event_loop().run_in_executor(
            None, _measure_loudness, prenorm
        )
        if measurement is None:
            # Fallback one-pass si la medición falla.
            ln = f"loudnorm=I={LOUDNORM_I}:TP={LOUDNORM_TP}:LRA={LOUDNORM_LRA}"
        else:
            ln = (
                f"loudnorm=I={LOUDNORM_I}:TP={LOUDNORM_TP}:LRA={LOUDNORM_LRA}"
                f":measured_I={measurement['input_i']}"
                f":measured_TP={measurement['input_tp']}"
                f":measured_LRA={measurement['input_lra']}"
                f":measured_thresh={measurement['input_thresh']}"
                f":offset={measurement['target_offset']}:linear=true"
            )
        ln_cmd = [
            "ffmpeg", "-y", "-hide_banner", "-nostats", "-i", str(prenorm),
            "-c:v", "copy", "-af", ln,
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart", str(output_path),
        ]
        proc2 = await asyncio.create_subprocess_exec(
            *ln_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr2 = await proc2.communicate()
        if proc2.returncode != 0:
            # Si loudnorm falla, conserva el render sin normalizar antes de abortar.
            prenorm.replace(output_path)
        else:
            prenorm.unlink(missing_ok=True)

    # ---- Intro/outro animados (HyperFrames) --------------------------------
    # Solo en render full: el preview rápido se mantiene ligero.
    if quality != "quick":
        try:
            from app.services import motion_service
            await motion_service.concat_intro_outro(
                project_id, output_path, config.get("aspect", "9:16"))
        except Exception as e:
            log.warning(f"Concat intro/outro omitido: {e}")

    size_mb = round(output_path.stat().st_size / 1024 / 1024, 1)
    meta["output"] = str(output_path)
    project_service.update_layer_status(project_id, "video", LayerStatus.ready, {
        "output": str(output_path),
        "size_mb": size_mb,
    })
    return output_path
