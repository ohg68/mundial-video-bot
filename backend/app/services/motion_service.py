"""Motion graphics vía el microservicio HyperFrames (intros/outros animados).

Setup:
- `HYPERFRAMES_URL`: URL del servicio (p.ej. http://hyperframes.railway.internal:8090).
  Sin esta variable el módulo queda deshabilitado y el render sigue igual que siempre.
"""
import os
import asyncio
import logging
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger(__name__)

MOTION_KINDS = ("intro", "outro", "captions")
# captions es WebM (VP9 + canal alpha para overlay); intro/outro MP4 opacos.
MOTION_FILES = {"intro": "intro.mp4", "outro": "outro.mp4", "captions": "captions.webm"}
RENDER_TIMEOUT = 300  # los renders de HyperFrames tardan ~20-60s


def service_url() -> Optional[str]:
    url = os.getenv("HYPERFRAMES_URL")
    return url.rstrip("/") if url else None


def is_configured() -> bool:
    return bool(service_url())


# El sondeo es caro comparado con pintar un botón, así que se recuerda un rato.
# Corto a propósito: si recreás el servicio, la interfaz se entera en un minuto
# sin tener que redesplegar.
_SONDEO: dict = {"vivo": None, "cuando": 0.0}
_SONDEO_TTL = 60


async def is_available() -> bool:
    """True si el servicio responde de verdad, no sólo si la variable está puesta.

    `is_configured()` sólo miraba que HYPERFRAMES_URL existiera, así que cuando
    el microservicio desapareció de Railway la variable siguió ahí y el estado
    seguía diciendo que sí: la interfaz ofrecía intros y captions kinéticos que
    fallaban recién al usarlos. Comprobar que conteste es la diferencia entre
    "configurado" y "disponible".
    """
    import time

    base = service_url()
    if not base:
        return False

    ahora = time.time()
    if _SONDEO["vivo"] is not None and ahora - _SONDEO["cuando"] < _SONDEO_TTL:
        return _SONDEO["vivo"]

    vivo = False
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"{base}/health")
            vivo = resp.status_code < 500
    except Exception as e:
        # Nivel info, no warning: con el servicio dado de baja esto pasa en cada
        # sondeo y llenaría los logs de ruido esperado.
        log.info("HyperFrames no responde en %s (%s)", base, type(e).__name__)

    _SONDEO.update(vivo=vivo, cuando=ahora)
    return vivo


def motion_dir(project_id: str) -> Path:
    return Path("projects") / project_id / "motion"


def motion_path(project_id: str, kind: str) -> Path:
    return motion_dir(project_id) / MOTION_FILES.get(kind, f"{kind}.mp4")


async def generate_motion(project_id: str, kind: str, variables: dict) -> Path:
    """Pide al microservicio el render de una intro/outro y lo guarda en el proyecto."""
    if kind not in ("intro", "outro"):
        raise ValueError(f"kind inválido: {kind}")
    base = service_url()
    if not base:
        raise RuntimeError("HYPERFRAMES_URL no configurado")

    async with httpx.AsyncClient(timeout=RENDER_TIMEOUT) as client:
        resp = await client.post(
            f"{base}/render",
            json={"template": kind, "variables": variables or {}},
        )
    if resp.status_code != 200:
        try:
            detail = resp.json().get("error", resp.text[:200])
        except Exception:
            detail = resp.text[:200]
        raise RuntimeError(f"HyperFrames error ({resp.status_code}): {detail}")

    dest = motion_path(project_id, kind)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)
    log.info(f"Motion '{kind}' generado para {project_id} ({len(resp.content)} bytes)")

    # Persistir en Cloudinary (disco efímero en Railway)
    from app.services import cloud_storage
    await cloud_storage.upload_layer_file(project_id, "motion", dest)

    return dest


async def restore_motion(project_id: str) -> int:
    """Restaura intro/outro desde Cloudinary si faltan en disco."""
    from app.services import cloud_storage
    if not cloud_storage.is_configured():
        return 0
    restored = 0
    for kind in MOTION_KINDS:
        dest = motion_path(project_id, kind)
        if dest.exists() and dest.stat().st_size > 0:
            continue
        public_id = f"layercut/projects/{project_id}/motion/{kind}"
        try:
            ok = await asyncio.to_thread(
                cloud_storage._download, public_id, "video", dest
            )
            if ok:
                restored += 1
        except Exception:
            pass
    return restored


async def generate_captions(project_id: str, accent_color: str = "#f5c518") -> Path:
    """Genera subtítulos kinéticos (WebM con alpha) desde los word-timestamps.

    Requiere audio generado y subtitles/words.json (lo escribe generate_subtitles).
    En el render full, si existe motion/captions.webm se superpone en lugar
    de los subtítulos ASS clásicos.
    """
    import json as _json

    base = service_url()
    if not base:
        raise RuntimeError("HYPERFRAMES_URL no configurado")

    from app.services import project_service
    words_path = project_service.get_layer_path(project_id, "subtitles").parent / "words.json"
    if not words_path.exists():
        raise RuntimeError("No hay words.json — genera los subtítulos primero")

    words = _json.loads(words_path.read_text(encoding="utf-8"))
    if not words:
        raise RuntimeError("words.json vacío")

    audio_path = project_service.get_layer_path(project_id, "audio")
    duration = await _probe_duration(audio_path) if audio_path.exists() else 0
    if duration <= 0:
        duration = max(w["e"] for w in words) + 0.5

    async with httpx.AsyncClient(timeout=RENDER_TIMEOUT) as client:
        resp = await client.post(
            f"{base}/render",
            json={
                "template": "captions",
                "format": "webm",
                "duration": round(duration, 2),
                "payload": {"words": words, "accentColor": accent_color},
            },
        )
    if resp.status_code != 200:
        try:
            detail = resp.json().get("error", resp.text[:200])
        except Exception:
            detail = resp.text[:200]
        raise RuntimeError(f"HyperFrames error ({resp.status_code}): {detail}")

    dest = motion_path(project_id, "captions")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)
    log.info(f"Captions kinéticos generados para {project_id} ({len(resp.content)} bytes)")

    from app.services import cloud_storage
    await cloud_storage.upload_layer_file(project_id, "motion", dest)

    return dest


async def _probe_duration(path: Path) -> float:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
        "-of", "csv=p=0", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    try:
        return float(stdout.decode().strip())
    except ValueError:
        return 0.0


async def concat_intro_outro(project_id: str, main_path: Path, aspect: str = "9:16") -> Optional[Path]:
    """Concatena intro + video principal + outro (los que existan).

    Re-encodea los tres segmentos a parámetros uniformes y les añade audio
    silencioso a intro/outro para que el concat no rompa el stream de audio.
    Devuelve la ruta del archivo combinado, o None si no hay intro ni outro.
    """
    intro = motion_path(project_id, "intro")
    outro = motion_path(project_id, "outro")
    has_intro = intro.exists() and intro.stat().st_size > 0
    has_outro = outro.exists() and outro.stat().st_size > 0
    if not has_intro and not has_outro:
        return None

    w, h = (1080, 1920) if aspect == "9:16" else (1920, 1080)
    segments = []
    if has_intro:
        segments.append(intro)
    segments.append(main_path)
    if has_outro:
        segments.append(outro)

    inputs = []
    filter_parts = []
    concat_refs = []

    for i, seg in enumerate(segments):
        inputs += ["-i", str(seg)]
        filter_parts.append(
            f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},fps=30,format=yuv420p,settb=AVTB[v{i}]"
        )
        if seg == main_path:
            filter_parts.append(f"[{i}:a]aresample=48000[a{i}]")
        else:
            # intro/outro no traen audio: generar silencio de su misma duración
            dur = await _probe_duration(seg)
            filter_parts.append(
                f"anullsrc=channel_layout=stereo:sample_rate=48000,"
                f"atrim=duration={dur:.2f}[a{i}]"
            )
        concat_refs.append(f"[v{i}][a{i}]")

    filter_parts.append(
        f"{''.join(concat_refs)}concat=n={len(segments)}:v=1:a=1[vout][aout]"
    )

    combined = main_path.with_name(main_path.stem + "_motion.mp4")
    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", ";".join(filter_parts),
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-crf", "22", "-preset", "fast",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart",
        str(combined),
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        log.warning(f"Concat intro/outro falló: {stderr.decode()[-400:]}")
        return None

    # Sustituir el output original por el combinado
    combined.replace(main_path)
    log.info(f"Intro/outro concatenados en {main_path.name} (proyecto {project_id})")
    return main_path
