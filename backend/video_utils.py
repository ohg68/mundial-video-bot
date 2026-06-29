"""video_utils.py — Utilidades de render FFmpeg para LayerCut.

Funciones portables extraídas del proyecto open-source video-use
(browser-use/video-use, licencia MIT), reescritas y limpiadas para integrarse
en un pipeline propio sin dependencias del flujo de agente ni de ElevenLabs.

Incluye:
  - Detección y tone-mapping HDR -> SDR (evita el sobresaturado de footage de móvil).
  - Auto color-grade data-driven por clip (corrección acotada a +-8%, "limpio sin
    parecer con grading").
  - Normalización de loudness two-pass a estándar de redes (-14 LUFS / -1 dBTP / LRA 11).
  - Extracción por segmento con grade + fades de audio de 30ms horneados.
  - Concat lossless y compositing final (overlays con PTS-shift + subtítulos al final).
  - Estilo de subtítulos vertical-safe (MarginV=90, fuera de la UI de TikTok/Reels/Shorts).

Requiere: ffmpeg y ffprobe en el PATH. Sin dependencias de terceros (solo stdlib).

Autor de la adaptación: para el pipeline LayerCut. Código base MIT.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path


# ===========================================================================
# 1. TONE-MAPPING HDR -> SDR
# ===========================================================================
#
# Los móviles (iPhone graba HLG por defecto, muchas mirrorless graban PQ)
# producen vídeo HDR en Rec.2020. Si solo se baja el bit depth (yuv420p10le
# -> yuv420p) SIN tone-mapping, el output queda 8-bit pero arrastra metadata
# de transferencia HLG/PQ. Los reproductores y los re-encodes de redes sociales
# que respetan esa metadata interpretan los valores de 8-bit como HDR y el
# resultado sale SOBRESATURADO / QUEMADO. QuickTime en macOS lo oculta en local,
# pero el screen recording y los uploads NO.
#
# Solución: detectar HDR por color_transfer y anteponer una cadena
# zscale + tonemap para que el output sea Rec.709 SDR limpio.

HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}  # PQ (HDR10) y HLG

TONEMAP_CHAIN = (
    "zscale=t=linear:npl=100,"
    "format=gbrpf32le,"
    "zscale=p=bt709,"
    "tonemap=tonemap=hable:desat=0,"
    "zscale=t=bt709:m=bt709:r=tv,"
    "format=yuv420p"
)


def is_hdr_source(video: Path) -> bool:
    """Devuelve True si el vídeo usa una función de transferencia PQ o HLG."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=color_transfer",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
            capture_output=True, text=True, check=True,
        )
        return out.stdout.strip() in HDR_TRANSFERS
    except subprocess.CalledProcessError:
        return False


def is_portrait_source(video: Path) -> bool:
    """Devuelve True si el vídeo es vertical (alto > ancho)."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-of", "csv=p=0", str(video)],
            capture_output=True, text=True, check=True,
        )
        w, h = map(int, out.stdout.strip().split(","))
        return h > w
    except Exception:
        return False


def probe_duration(video: Path) -> float:
    """Duración del vídeo en segundos (10.0 como fallback si falla)."""
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video)]
        )
        return float(out.decode().strip())
    except Exception:
        return 10.0


# ===========================================================================
# 2. AUTO COLOR-GRADE (data-driven, por clip)
# ===========================================================================
#
# En vez de aplicar un LUT/preset fijo a footage heterogéneo, se analiza cada
# clip con signalstats de ffmpeg (brillo medio, rango/contraste, saturación),
# se normaliza por el bit-depth nativo y se emite una corrección ACOTADA a +-8%
# en cualquier eje. Filosofía: "limpio sin que parezca con grading". Corrige
# subexposición (lift gamma), planitud (contraste) y desaturación. NUNCA aplica
# desplazamientos creativos de color (teal/orange, LUTs filmicas).
#
# Para looks creativos, usa PRESETS explícitamente.

PRESETS: dict[str, str] = {
    # Limpieza apenas perceptible. Sin desplazamiento de color.
    "subtle": "eq=contrast=1.03:saturation=0.98",

    # Grade correctivo mínimo: contraste ligero + curva S suave, sin cambios de tono.
    "neutral_punch": (
        "eq=contrast=1.06:brightness=0.0:saturation=1.0,"
        "curves=master='0/0 0.25/0.23 0.75/0.77 1/1'"
    ),

    # Preset creativo OPT-IN para look retro/cinematográfico. No es default.
    # +12% contraste, negros aplastados, -12% sat, sombras cálidas + altas frías.
    "warm_cinematic": (
        "eq=contrast=1.12:brightness=-0.02:saturation=0.88,"
        "colorbalance="
        "rs=0.02:gs=0.0:bs=-0.03:"
        "rm=0.04:gm=0.01:bm=-0.02:"
        "rh=0.08:gh=0.02:bh=-0.05,"
        "curves=master='0/0 0.25/0.22 0.75/0.78 1/1'"
    ),

    # Sin grade. Útil como centinela para "saltar grading en esta fuente".
    "none": "",
}


def get_preset(name: str) -> str:
    """Devuelve el filtro ffmpeg de un preset. Cadena vacía para 'none'."""
    if name not in PRESETS:
        raise KeyError(f"preset desconocido '{name}'. Disponibles: {', '.join(sorted(PRESETS))}")
    return PRESETS[name]


def _sample_frame_stats(
    video: Path, start: float, duration: float, n_samples: int = 10,
) -> dict[str, float]:
    """Muestrea N frames de un rango y calcula brillo/contraste/saturación.

    Usa el filtro signalstats de ffmpeg (YMIN, YMAX, YAVG, SATAVG) y promedia
    sobre las muestras. Normaliza por el bit-depth nativo del frame decodificado.

    Devuelve: {"y_mean", "y_std", "sat_mean"} todos en 0..1.
    """
    fps = max(0.5, min(n_samples / max(duration, 0.1), 10.0))

    with tempfile.NamedTemporaryFile(mode="w+", suffix=".txt", delete=False) as f:
        metadata_path = f.name

    try:
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-nostats",
            "-ss", f"{start:.3f}", "-i", str(video), "-t", f"{duration:.3f}",
            "-vf", f"fps={fps:.2f},signalstats,metadata=print:file={metadata_path}",
            "-f", "null", "-",
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        y_avgs: list[float] = []
        y_mins: list[float] = []
        y_maxs: list[float] = []
        sat_avgs: list[float] = []
        bit_depth: int = 8

        def _parse_value(line: str) -> float | None:
            try:
                return float(line.rsplit("=", 1)[1])
            except (ValueError, IndexError):
                return None

        with open(metadata_path) as fh:
            for line in fh:
                line = line.strip()
                if "lavfi.signalstats.YBITDEPTH" in line:
                    v = _parse_value(line)
                    if v is not None:
                        bit_depth = int(v)
                elif "lavfi.signalstats.YAVG" in line:
                    v = _parse_value(line)
                    if v is not None:
                        y_avgs.append(v)
                elif "lavfi.signalstats.YMIN" in line:
                    v = _parse_value(line)
                    if v is not None:
                        y_mins.append(v)
                elif "lavfi.signalstats.YMAX" in line:
                    v = _parse_value(line)
                    if v is not None:
                        y_maxs.append(v)
                elif "lavfi.signalstats.SATAVG" in line:
                    v = _parse_value(line)
                    if v is not None:
                        sat_avgs.append(v)

        if not y_avgs:
            return {"y_mean": 0.5, "y_std": 0.18, "sat_mean": 0.25}

        max_val = (2 ** bit_depth) - 1
        y_mean = (sum(y_avgs) / len(y_avgs)) / max_val
        y_range = (
            ((sum(y_maxs) / len(y_maxs)) - (sum(y_mins) / len(y_mins))) / max_val
            if y_maxs and y_mins else 0.7
        )
        sat_mean = ((sum(sat_avgs) / len(sat_avgs)) / max_val) if sat_avgs else 0.25

        return {"y_mean": y_mean, "y_std": y_range / 4.0, "sat_mean": sat_mean}
    finally:
        Path(metadata_path).unlink(missing_ok=True)


def auto_grade_for_clip(
    video: Path, start: float = 0.0, duration: float | None = None, verbose: bool = False,
) -> tuple[str, dict[str, float]]:
    """Analiza un rango y emite un filtro de corrección sutil por clip.

    Devuelve (filtro, stats). El filtro está acotado a +-8% en cualquier eje y
    NO aplica desplazamiento de color. Solo corrige subexposición, planitud y
    desaturación. Si el clip ya está equilibrado, devuelve corrección mínima.
    """
    if duration is None:
        duration = probe_duration(video)

    stats = _sample_frame_stats(video, start, duration)
    y_mean = stats["y_mean"]
    y_range = stats["y_std"] * 4.0
    sat_mean = stats["sat_mean"]

    # Contraste: objetivo y_range ~ 0.72. Sube suave si está plano, nunca baja.
    if y_range < 0.65:
        t = max(0.0, min(1.0, (y_range - 0.50) / 0.15))
        contrast_adj = 1.08 - 0.05 * t
    else:
        contrast_adj = 1.03

    # Gamma: objetivo y_mean ~ 0.48. Lift suave si está oscuro.
    gamma_adj = 1.0
    if y_mean < 0.42:
        t = max(0.0, min(1.0, (y_mean - 0.30) / 0.12))
        gamma_adj = 1.10 - 0.08 * t
    elif y_mean > 0.60:
        gamma_adj = 0.97

    # Saturación: objetivo sat_mean ~ 0.25. Nunca desatura agresivo.
    sat_adj = 0.98
    if sat_mean < 0.18:
        sat_adj = 1.04
    elif sat_mean > 0.38:
        sat_adj = 0.96

    # Clamp duro (+-8% efectivo)
    contrast_adj = max(0.94, min(1.08, contrast_adj))
    gamma_adj = max(0.94, min(1.10, gamma_adj))
    sat_adj = max(0.94, min(1.06, sat_adj))

    eq_parts = []
    if abs(contrast_adj - 1.0) > 0.005:
        eq_parts.append(f"contrast={contrast_adj:.3f}")
    if abs(gamma_adj - 1.0) > 0.005:
        eq_parts.append(f"gamma={gamma_adj:.3f}")
    if abs(sat_adj - 1.0) > 0.005:
        eq_parts.append(f"saturation={sat_adj:.3f}")

    filter_string = ("eq=" + ":".join(eq_parts)) if eq_parts else ""

    if verbose:
        print(f"  auto-grade: y_mean={y_mean:.3f} y_range={y_range:.3f} sat={sat_mean:.3f}")
        print(f"    -> contrast={contrast_adj:.3f} gamma={gamma_adj:.3f} sat={sat_adj:.3f}")
        print(f"    -> filtro: {filter_string or '(vacío)'}")

    return filter_string, stats


# ===========================================================================
# 3. NORMALIZACIÓN DE LOUDNESS (two-pass, social-ready)
# ===========================================================================
#
# Estándar de redes sociales: -14 LUFS integrado, -1 dBTP pico, LRA 11 LU.
# Coincide con los targets de normalización de YouTube/IG/TikTok/X/LinkedIn.
# Imprescindible si la narración (p.ej. Edge TTS) tiene niveles inconsistentes.

LOUDNORM_I = -14.0
LOUDNORM_TP = -1.0
LOUDNORM_LRA = 11.0


def measure_loudness(video_path: Path) -> dict[str, str] | None:
    """Primera pasada de loudnorm; parsea la medición JSON. None si falla."""
    filter_str = (
        f"loudnorm=I={LOUDNORM_I}:TP={LOUDNORM_TP}:LRA={LOUDNORM_LRA}:print_format=json"
    )
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-nostats",
        "-i", str(video_path), "-af", filter_str, "-vn", "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    stderr = proc.stderr
    start = stderr.rfind("{")
    end = stderr.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        data = json.loads(stderr[start:end + 1])
    except json.JSONDecodeError:
        return None
    needed = {"input_i", "input_tp", "input_lra", "input_thresh", "target_offset"}
    if not needed.issubset(data.keys()):
        return None
    return data


def apply_loudnorm_two_pass(
    input_path: Path, output_path: Path, preview: bool = False,
) -> bool:
    """Normaliza loudness con two-pass. Copia vídeo, re-codifica solo audio.

    En modo preview usa una aproximación one-pass (más rápida). El modo final
    siempre hace las dos pasadas. Devuelve True si tuvo éxito.
    """
    if preview:
        filter_str = f"loudnorm=I={LOUDNORM_I}:TP={LOUDNORM_TP}:LRA={LOUDNORM_LRA}"
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-nostats", "-i", str(input_path),
            "-c:v", "copy", "-af", filter_str,
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart", str(output_path),
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return True

    measurement = measure_loudness(input_path)
    if measurement is None:
        return apply_loudnorm_two_pass(input_path, output_path, preview=True)

    filter_str = (
        f"loudnorm=I={LOUDNORM_I}:TP={LOUDNORM_TP}:LRA={LOUDNORM_LRA}"
        f":measured_I={measurement['input_i']}"
        f":measured_TP={measurement['input_tp']}"
        f":measured_LRA={measurement['input_lra']}"
        f":measured_thresh={measurement['input_thresh']}"
        f":offset={measurement['target_offset']}"
        f":linear=true"
    )
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-nostats", "-i", str(input_path),
        "-c:v", "copy", "-af", filter_str,
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart", str(output_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return True


# ===========================================================================
# 4. SUBTÍTULOS — estilo vertical-safe
# ===========================================================================
#
# MarginV NO es estética, es regla de safe-zone. La UI de TikTok/Reels/Shorts
# (caption, usuario, música, botones laterales) cubre ~25-30% inferior del frame
# 1080x1920. libass escala el canvas respecto a PlayResY=288, así que MarginV=90
# deja la línea base del subtítulo ~30% desde abajo en cualquier aspect ratio,
# libre de la UI. No bajar de ~75 sin razón específica.

SUB_FORCE_STYLE = (
    "FontName=Helvetica,FontSize=18,Bold=1,"
    "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H00000000,"
    "BorderStyle=1,Outline=2,Shadow=0,"
    "Alignment=2,MarginV=90"
)


# ===========================================================================
# 5. EXTRACCIÓN POR SEGMENTO + CONCAT + COMPOSITING
# ===========================================================================
#
# El orden canónico que evita el bug de conflicto de filtros:
#   1. Extraer cada segmento con grade + fades de 30ms HORNEADOS dentro.
#   2. Concat lossless -c copy (cero re-encode).
#   3. Un solo filter_complex: overlays con PTS-shift + subtítulos AL FINAL.
#   4. Loudnorm como pasada separada al final.


def extract_segment(
    source: Path,
    seg_start: float,
    duration: float,
    out_path: Path,
    grade_filter: str = "",
    quality: str = "final",  # "final" | "preview" | "draft"
    target_height: int | None = None,
    fps: int = 24,
) -> None:
    """Extrae un rango como MP4 propio con grade + fades de audio de 30ms.

    -ss antes de -i para seek rápido y preciso. Tone-mapping HDR automático.
    Las fuentes verticales se escalan por altura para preservar orientación.

    quality:
      - "final":   target_height(o 1920) libx264 fast CRF 20
      - "preview": 1080p libx264 medium CRF 22 (evaluable para QC)
      - "draft":   720p libx264 ultrafast CRF 28 (solo verificar cortes)
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    portrait = is_portrait_source(source)

    if quality == "draft":
        h = 1280
        preset, crf = "ultrafast", "28"
    elif quality == "preview":
        h = target_height or 1920
        preset, crf = "medium", "22"
    else:  # final
        h = target_height or 1920
        preset, crf = "fast", "20"

    scale = f"scale=-2:{h}" if portrait else f"scale={int(h * 16 / 9)}:-2"

    vf_parts: list[str] = []
    if is_hdr_source(source):
        vf_parts.append(TONEMAP_CHAIN)
    vf_parts.append(scale)
    if grade_filter:
        vf_parts.append(grade_filter)
    vf = ",".join(vf_parts)

    # 30ms de fade de audio en ambos bordes -> evita pops
    fade_out_start = max(0.0, duration - 0.03)
    af = f"afade=t=in:st=0:d=0.03,afade=t=out:st={fade_out_start:.3f}:d=0.03"

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{seg_start:.3f}", "-i", str(source), "-t", f"{duration:.3f}",
        "-vf", vf, "-af", af,
        "-c:v", "libx264", "-preset", preset, "-crf", crf,
        "-pix_fmt", "yuv420p", "-r", str(fps),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart", str(out_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def concat_segments(segment_paths: list[Path], out_path: Path, work_dir: Path) -> None:
    """Concat lossless vía demuxer concat. Sin re-encode."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    concat_list = work_dir / "_concat.txt"
    concat_list.write_text("".join(f"file '{p.resolve()}'\n" for p in segment_paths))
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_list), "-c", "copy",
        "-movflags", "+faststart", str(out_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    concat_list.unlink(missing_ok=True)


def build_final_composite(
    base_path: Path,
    out_path: Path,
    overlays: list[dict] | None = None,
    subtitles_path: Path | None = None,
    work_dir: Path | None = None,
) -> None:
    """Pasada final: base -> overlays (PTS-shift) -> subtítulos AL FINAL -> out.

    overlays: lista de dicts {"file": ruta, "start_in_output": seg, "duration": seg}.
    subtitles_path: ruta a un .srt. Se aplica SIEMPRE el último en la cadena
    (si no, los overlays lo taparían — fallo silencioso).
    """
    overlays = overlays or []
    work_dir = work_dir or out_path.parent
    has_overlays = bool(overlays)
    has_subs = subtitles_path is not None and subtitles_path.exists()

    if not has_overlays and not has_subs:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(base_path), "-c", "copy", str(out_path)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        return

    inputs: list[str] = ["-i", str(base_path)]
    for ov in overlays:
        inputs += ["-i", str(Path(ov["file"]).resolve())]

    filter_parts: list[str] = []
    # PTS-shift de cada overlay para que su frame 0 caiga en start_in_output
    for idx, ov in enumerate(overlays, start=1):
        t = float(ov["start_in_output"])
        filter_parts.append(f"[{idx}:v]setpts=PTS-STARTPTS+{t}/TB[a{idx}]")

    current = "[0:v]"
    for idx, ov in enumerate(overlays, start=1):
        t = float(ov["start_in_output"])
        end = t + float(ov["duration"])
        next_label = f"[v{idx}]"
        filter_parts.append(
            f"{current}[a{idx}]overlay=enable='between(t,{t:.3f},{end:.3f})'{next_label}"
        )
        current = next_label

    # Subtítulos AL FINAL
    if has_subs:
        subs_abs = str(subtitles_path.resolve()).replace(":", r"\:").replace("'", r"\'")
        filter_parts.append(
            f"{current}subtitles='{subs_abs}':force_style='{SUB_FORCE_STYLE}'[outv]"
        )
        out_label = "[outv]"
    else:
        if has_overlays:
            filter_parts.append(f"{current}null[outv]")
            out_label = "[outv]"
        else:
            out_label = "[0:v]"

    filter_complex = ";".join(filter_parts)
    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", filter_complex,
        "-map", out_label, "-map", "0:a",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-c:a", "copy",
        "-movflags", "+faststart", str(out_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


# ===========================================================================
# 6. PIPELINE DE CONVENIENCIA
# ===========================================================================


def render_segments(
    segments: list[dict],
    out_path: Path,
    work_dir: Path,
    grade: str = "auto",  # "auto" | nombre de preset | filtro raw | "none"
    overlays: list[dict] | None = None,
    subtitles_path: Path | None = None,
    quality: str = "final",
    loudnorm: bool = True,
    target_height: int | None = None,
) -> Path:
    """Renderiza un vídeo final desde una lista de segmentos.

    segments: [{"source": Path|str, "start": float, "end": float}, ...]
    grade: "auto" analiza cada segmento; o nombre de preset; o filtro ffmpeg raw.

    Devuelve la ruta del output final.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    clips_dir = work_dir / "clips"
    clips_dir.mkdir(exist_ok=True)

    # Resolver el modo de grade
    is_auto = grade == "auto"
    if not is_auto and grade:
        if re.fullmatch(r"[a-zA-Z0-9_\-]+", grade):
            try:
                resolved_grade = get_preset(grade)
            except KeyError:
                resolved_grade = grade  # tratar como filtro raw
        else:
            resolved_grade = grade
    else:
        resolved_grade = ""

    seg_paths: list[Path] = []
    for i, seg in enumerate(segments):
        src = Path(seg["source"])
        start = float(seg["start"])
        dur = float(seg["end"]) - start
        seg_out = clips_dir / f"seg_{i:02d}.mp4"

        if is_auto:
            gfilter, _ = auto_grade_for_clip(src, start=start, duration=dur)
        else:
            gfilter = resolved_grade

        extract_segment(
            src, start, dur, seg_out,
            grade_filter=gfilter, quality=quality, target_height=target_height,
        )
        seg_paths.append(seg_out)

    base_path = work_dir / "base.mp4"
    concat_segments(seg_paths, base_path, work_dir)

    if loudnorm:
        tmp = out_path.with_suffix(".prenorm.mp4")
        build_final_composite(base_path, tmp, overlays, subtitles_path, work_dir)
        apply_loudnorm_two_pass(tmp, out_path, preview=(quality != "final"))
        tmp.unlink(missing_ok=True)
    else:
        build_final_composite(base_path, out_path, overlays, subtitles_path, work_dir)

    return out_path


# ===========================================================================
# EJEMPLO DE USO
# ===========================================================================
if __name__ == "__main__":
    # Ejemplo: ensamblar tres cortes con auto-grade, subtítulos y loudnorm.
    #
    # from pathlib import Path
    # segments = [
    #     {"source": "footage/clip_a.mp4", "start": 2.4, "end": 6.8},
    #     {"source": "footage/clip_b.mp4", "start": 0.0, "end": 4.2},
    #     {"source": "footage/clip_c.mp4", "start": 10.1, "end": 15.5},
    # ]
    # overlays = [
    #     {"file": "anim/logo.mp4", "start_in_output": 0.0, "duration": 3.0},
    # ]
    # render_segments(
    #     segments,
    #     out_path=Path("out/final.mp4"),
    #     work_dir=Path("out/work"),
    #     grade="auto",
    #     overlays=overlays,
    #     subtitles_path=Path("out/captions.srt"),
    #     quality="final",
    #     loudnorm=True,
    #     target_height=1920,   # vertical 9:16
    # )
    print("video_utils.py — módulo de utilidades FFmpeg para LayerCut. Importar, no ejecutar.")
