import asyncio
import json
import logging
import shutil
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.database import SessionLocal, MediaAsset
from app.services.layer_service import _get_audio_duration as probe_duration

log = logging.getLogger(__name__)

LIBRARY_DIR = Path("media_library")
LIBRARY_DIR.mkdir(exist_ok=True)

# Dominios que yt-dlp sabe resolver (video "compuesto" por streams, requiere
# extracción). Cualquier otra URL se trata como descarga directa por HTTP.
_YTDLP_DOMAINS = (
    "youtube.com", "youtu.be", "vimeo.com", "tiktok.com",
    "twitter.com", "x.com", "instagram.com", "facebook.com", "twitch.tv",
)


def _detect_source_type(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if any(host == d or host.endswith("." + d) for d in _YTDLP_DOMAINS):
        return "youtube"
    return "direct"


def _video_path(asset_id: str) -> Path:
    return LIBRARY_DIR / f"{asset_id}.mp4"


def _thumb_path(asset_id: str) -> Path:
    return LIBRARY_DIR / f"{asset_id}_thumb.jpg"


def _update(asset_id: str, **fields):
    db = SessionLocal()
    try:
        asset = db.query(MediaAsset).filter(MediaAsset.id == asset_id).first()
        if not asset:
            return
        for k, v in fields.items():
            setattr(asset, k, v)
        db.commit()
    finally:
        db.close()


async def _generate_thumbnail(video_path: Path, dest: Path, timestamp: float = 1.0) -> bool:
    """Captura un frame del video como thumbnail. Generaliza el patrón usado
    en publish_service.generate_thumbnail para un path arbitrario (no atado a
    un project_id)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-ss", str(timestamp), "-i", str(video_path),
        "-vframes", "1", "-q:v", "2", str(dest),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    return dest.exists() and dest.stat().st_size > 0


async def _probe_dimensions(path: Path) -> tuple[int, int]:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "quiet", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0",
        str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    try:
        w, h = stdout.decode().strip().split("x")
        return int(w), int(h)
    except Exception:
        return 0, 0


async def import_from_url(asset_id: str, url: str):
    """Background task: descarga `url` a media_library/{asset_id}.mp4, prueba
    duración/resolución, genera thumbnail. No implementa ningún bypass de
    DRM/autenticación — si yt-dlp o la descarga directa fallan (contenido
    protegido, geo-bloqueado, etc.), el asset simplemente queda en error con
    el mensaje real, sin reintentos con credenciales."""
    source_type = _detect_source_type(url)
    dest = _video_path(asset_id)
    _update(asset_id, status="downloading", source_type=source_type)

    try:
        if source_type == "youtube":
            proc = await asyncio.create_subprocess_exec(
                "yt-dlp", "-f", "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
                "--merge-output-format", "mp4",
                "--no-playlist",
                "-o", str(dest),
                url,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0 or not dest.exists():
                _update(asset_id, status="error", error=stderr.decode()[-500:] or "yt-dlp falló")
                return
        else:
            headers = {"User-Agent": "Mozilla/5.0 (compatible; LayerCutImporter/1.0)"}
            async with httpx.AsyncClient(follow_redirects=True, timeout=120, headers=headers) as client:
                resp = await client.get(url)
            if resp.status_code != 200 or len(resp.content) < 10000:
                _update(asset_id, status="error",
                        error=f"Descarga directa falló (HTTP {resp.status_code})")
                return
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(resp.content)
    except FileNotFoundError:
        _update(asset_id, status="error", error="yt-dlp no está instalado en el servidor.")
        return
    except Exception as e:
        _update(asset_id, status="error", error=str(e)[:500])
        return

    duration = await probe_duration(dest)
    width, height = await _probe_dimensions(dest)
    thumb = _thumb_path(asset_id)
    has_thumb = await _generate_thumbnail(dest, thumb, timestamp=min(1.0, duration / 2 if duration else 1.0))

    _update(
        asset_id, status="ready",
        duration=duration, width=width, height=height,
        file_path=str(dest),
        thumbnail_path=str(thumb) if has_thumb else None,
    )

    try:
        from app.services import cloud_storage
        await cloud_storage.upload_library_asset(asset_id, dest, thumb if has_thumb else None)
    except Exception as e:
        log.warning(f"Subida a Cloudinary del asset {asset_id} falló (se reintenta on-demand): {e}")


def create_pending_asset(url: str) -> dict:
    asset_id = uuid.uuid4().hex[:12]
    db = SessionLocal()
    try:
        asset = MediaAsset(id=asset_id, source_url=url, status="pending")
        db.add(asset)
        db.commit()
        db.refresh(asset)
        return asset.to_dict()
    finally:
        db.close()


def create_trim_child(parent: dict, start: float, end: float) -> dict:
    child_id = uuid.uuid4().hex[:12]
    db = SessionLocal()
    try:
        child = MediaAsset(
            id=child_id, parent_id=parent["id"], source_url=parent["source_url"],
            source_type=parent["source_type"], title=parent.get("title") or "",
            status="trimming", trim_start=start, trim_end=end,
        )
        db.add(child)
        db.commit()
        db.refresh(child)
        return child.to_dict()
    finally:
        db.close()


async def trim_asset(child_id: str, parent_id: str, start: float, end: float):
    """Background task: recorta [start,end) del asset padre con stream-copy
    (sin recodificar — el pipeline de render ya recodifica al usar el clip,
    recodificar acá sería trabajo duplicado). El corte cae en el keyframe más
    cercano, no es frame-exacto."""
    parent = get_asset(parent_id)
    if not parent or parent["status"] != "ready" or not parent.get("file_path"):
        _update(child_id, status="error", error="El asset original no está listo.")
        return

    src = Path(parent["file_path"])
    if not src.exists():
        try:
            from app.services import cloud_storage
            restored = await cloud_storage.restore_library_asset(
                parent_id, parent.get("cloud_video_public_id"), src)
            if not restored:
                raise RuntimeError("no restaurado")
        except Exception:
            _update(child_id, status="error",
                    error="El archivo original ya no está disponible localmente ni en la nube.")
            return

    dest = _video_path(child_id)
    duration = max(0.0, end - start)

    # Stream-copy primero (rápido, sin recodificar — el pipeline de render ya
    # recodifica al usar el clip). Si el códec de origen no es compatible con
    # mp4 (p. ej. Theora/VP8 en un contenedor viejo), cae a recodificar.
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{duration:.3f}",
        "-c", "copy", str(dest),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    copy_ok = proc.returncode == 0 and dest.exists() and dest.stat().st_size > 0

    if not copy_ok:
        log.info(f"Trim {child_id}: stream-copy falló, recodificando ({stderr.decode()[-200:]})")
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-crf", "23", "-preset", "fast",
            "-c:a", "aac", str(dest),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
            _update(child_id, status="error", error=stderr.decode()[-500:] or "ffmpeg falló al recortar")
            return

    real_duration = await probe_duration(dest)
    width, height = await _probe_dimensions(dest)
    thumb = _thumb_path(child_id)
    has_thumb = await _generate_thumbnail(dest, thumb, timestamp=min(1.0, real_duration / 2 if real_duration else 0.0))

    _update(
        child_id, status="ready",
        duration=real_duration or duration, width=width, height=height,
        file_path=str(dest), thumbnail_path=str(thumb) if has_thumb else None,
    )

    try:
        from app.services import cloud_storage
        await cloud_storage.upload_library_asset(child_id, dest, thumb if has_thumb else None)
    except Exception as e:
        log.warning(f"Subida a Cloudinary del recorte {child_id} falló (se reintenta on-demand): {e}")


def get_asset(asset_id: str) -> dict | None:
    db = SessionLocal()
    try:
        asset = db.query(MediaAsset).filter(MediaAsset.id == asset_id).first()
        return asset.to_dict() if asset else None
    finally:
        db.close()


def list_assets(status: str = None, q: str = None, limit: int = 50, offset: int = 0) -> dict:
    db = SessionLocal()
    try:
        query = db.query(MediaAsset)
        if status:
            query = query.filter(MediaAsset.status == status)
        if q:
            query = query.filter(MediaAsset.title.contains(q))
        total = query.count()
        rows = (query.order_by(MediaAsset.created_at.desc())
                .offset(offset).limit(limit).all())
        return {"assets": [r.to_dict() for r in rows], "total": total}
    finally:
        db.close()


def delete_asset(asset_id: str) -> bool:
    db = SessionLocal()
    try:
        asset = db.query(MediaAsset).filter(MediaAsset.id == asset_id).first()
        if not asset:
            return False
        # Cascada simple: los recortes derivados de este asset también se borran.
        children = db.query(MediaAsset).filter(MediaAsset.parent_id == asset_id).all()
        for child in [asset, *children]:
            for p in (child.file_path, child.thumbnail_path):
                if p and Path(p).exists():
                    Path(p).unlink(missing_ok=True)
            db.delete(child)
        db.commit()
        return True
    finally:
        db.close()
