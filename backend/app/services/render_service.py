import asyncio
import json
import re
import subprocess
from pathlib import Path
from app.services import project_service
from app.models.project import LayerStatus

# Estándar de redes sociales para loudness (YouTube/IG/TikTok/X).
LOUDNORM_I = -14.0
LOUDNORM_TP = -1.0
LOUDNORM_LRA = 11.0

# Transferencias HDR que requieren tone-mapping a SDR.
HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}  # PQ (HDR10) y HLG


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


def _escape_subs_path(p: Path) -> str:
    """Escapa la ruta del .srt/.vtt para el filtro subtitles de ffmpeg."""
    return str(p.resolve()).replace("\\", "\\\\").replace(":", r"\:").replace("'", r"\'")


async def render_final(project_id: str) -> Path:
    meta = project_service.get_project(project_id)
    if not meta:
        raise ValueError(f"Project {project_id} not found")
    config = meta["config"]

    project_dir = Path("projects") / project_id
    output_path = project_dir / "output" / "final.mp4"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    video_path = project_service.get_layer_path(project_id, "video")
    audio_path = project_service.get_layer_path(project_id, "audio")
    music_path = project_service.get_layer_path(project_id, "music")
    sub_path = project_service.get_layer_path(project_id, "subtitles")
    overlay_path = project_service.get_layer_path(project_id, "overlay")

    has_video = video_path.exists()
    has_audio = audio_path.exists()
    has_music = music_path.exists()
    has_subs = sub_path.exists()
    has_overlay = overlay_path.exists()

    if not has_video:
        raise ValueError("Video layer missing — cannot render")

    vol_narr = config.get("audio", {}).get("volume", 0.9)
    vol_music = config.get("music", {}).get("volume", 0.25)
    fade_out = config.get("music", {}).get("fade_out", 3)
    sub_cfg = config.get("subtitles", {})
    sub_font = sub_cfg.get("font", "DejaVu Sans")      # DejaVu: disponible en Linux/Railway
    sub_size = sub_cfg.get("font_size", 48)
    sub_margin = sub_cfg.get("margin_v", 90)           # safe-zone vertical (fuera de UI TikTok/Reels)

    # ---- Inputs -----------------------------------------------------------
    # Orden fijo: [0]=video, luego audio, music, overlay (si existen).
    inputs = ["-i", str(video_path)]
    idx_audio = idx_music = idx_overlay = None
    next_idx = 1
    if has_audio:
        inputs += ["-i", str(audio_path)]; idx_audio = next_idx; next_idx += 1
    if has_music:
        inputs += ["-i", str(music_path)]; idx_music = next_idx; next_idx += 1
    if has_overlay:
        inputs += ["-i", str(overlay_path)]; idx_overlay = next_idx; next_idx += 1

    # ---- Cadena de filtros UNIFICADA (un solo -filter_complex) ------------
    # Antes el código mezclaba -vf y -filter_complex: cuando coincidían
    # subtítulos + overlay, ffmpeg fallaba ("simple filtergraph ... 2 inputs").
    # Aquí todo va en filter_complex y los SUBTÍTULOS SE APLICAN AL FINAL,
    # después del overlay (si no, el overlay puede taparlos).
    fparts = []

    # --- Audio ---
    audio_map = None
    if has_audio:
        fparts.append(f"[{idx_audio}:a]volume={vol_narr}[narr]")
    if has_music:
        fparts.append(
            f"[{idx_music}:a]volume={vol_music},afade=t=out:st=85:d={fade_out}[music]"
        )
    if has_audio and has_music:
        fparts.append("[narr][music]amix=inputs=2:duration=first[aout]")
        audio_map = "[aout]"
    elif has_audio:
        audio_map = "[narr]"
    elif has_music:
        audio_map = "[music]"

    # --- Vídeo ---
    # Tone-mapping HDR->SDR si el video layer trae transferencia HLG/PQ
    # (relevante si entran clips locales de móvil; Pexels suele ser SDR).
    cur = "[0:v]"
    if _is_hdr(video_path):
        fparts.append(
            f"{cur}zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
            f"tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,"
            f"format=yuv420p[vtm]"
        )
        cur = "[vtm]"

    # Overlay (logo arriba-derecha) ANTES de subtítulos
    if has_overlay:
        fparts.append(f"[{idx_overlay}:v]format=rgba[logo]")
        fparts.append(f"{cur}[logo]overlay=W-w-20:20[vov]")
        cur = "[vov]"

    # Subtítulos SIEMPRE el último filtro de vídeo
    if has_subs:
        subs = _escape_subs_path(sub_path)
        style = (
            f"FontName={sub_font},FontSize={sub_size},Bold=1,"
            f"PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
            f"BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV={sub_margin}"
        )
        fparts.append(f"{cur}subtitles='{subs}':force_style='{style}'[vout]")
        video_map = "[vout]"
    else:
        # Sin filtros de vídeo: mapear el stream original directamente.
        video_map = cur if cur != "[0:v]" else "0:v"
        if cur != "[0:v]":
            # Asegurar una etiqueta de salida limpia.
            fparts.append(f"{cur}null[vout]")
            video_map = "[vout]"

    # ---- Render a archivo intermedio (pre-loudnorm) -----------------------
    prenorm = output_path.with_suffix(".prenorm.mp4")
    cmd = ["ffmpeg", "-y", *inputs]
    if fparts:
        cmd += ["-filter_complex", ";".join(fparts)]
    cmd += ["-map", video_map]
    if audio_map:
        cmd += ["-map", audio_map]
    cmd += [
        "-c:v", "libx264", "-crf", "22", "-preset", "fast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-shortest", "-movflags", "+faststart",
        str(prenorm),
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        prenorm.unlink(missing_ok=True)
        raise RuntimeError(f"FFmpeg error (compose): {stderr.decode()[-2000:]}")

    # ---- Loudness two-pass -> final ---------------------------------------
    # Estandariza el volumen de la mezcla narración+música a -14 LUFS.
    # Si no hay pista de audio, salta y solo copia.
    if audio_map is None:
        prenorm.replace(output_path)
    else:
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

    size_mb = round(output_path.stat().st_size / 1024 / 1024, 1)
    meta["output"] = str(output_path)
    project_service.update_layer_status(project_id, "video", LayerStatus.ready, {
        "output": str(output_path),
        "size_mb": size_mb,
    })
    return output_path
