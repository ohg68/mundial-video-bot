import os
import asyncio
import logging
import shutil
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

DB_FILENAME = "layercut.db"
LOCAL_DB_PATH = Path("./layercut.db")

# layer -> (filename, cloudinary resource_type)
LAYER_ASSETS = {
    "video": ("video.mp4", "video"),
    "audio": ("narration.mp3", "raw"),
    "music": ("music.mp3", "raw"),
    "subtitles": ("subtitles.srt", "raw"),
    "overlay": ("overlay.png", "raw"),
}


def _configure():
    url = os.getenv("CLOUDINARY_URL")
    if not url:
        cloud = os.getenv("CLOUDINARY_CLOUD_NAME")
        key = os.getenv("CLOUDINARY_API_KEY")
        secret = os.getenv("CLOUDINARY_API_SECRET")
        if not all([cloud, key, secret]):
            return False
        os.environ["CLOUDINARY_URL"] = f"cloudinary://{key}:{secret}@{cloud}"

    import cloudinary
    cloudinary.config(secure=True)
    return True


def is_configured() -> bool:
    return bool(
        os.getenv("CLOUDINARY_URL")
        or all([
            os.getenv("CLOUDINARY_CLOUD_NAME"),
            os.getenv("CLOUDINARY_API_KEY"),
            os.getenv("CLOUDINARY_API_SECRET"),
        ])
    )


def _upload_raw(local_path: Path, public_id: str) -> Optional[str]:
    if not _configure():
        return None
    import cloudinary.uploader
    result = cloudinary.uploader.upload(
        str(local_path),
        public_id=public_id,
        resource_type="raw",
        overwrite=True,
        invalidate=True,
    )
    return result.get("secure_url")


def _upload_video(local_path: Path, public_id: str) -> Optional[str]:
    if not _configure():
        return None
    import cloudinary.uploader
    result = cloudinary.uploader.upload_large(
        str(local_path),
        public_id=public_id,
        resource_type="video",
        overwrite=True,
        invalidate=True,
        chunk_size=20_000_000,
    )
    return result.get("secure_url")


def _download(public_id: str, resource_type: str, dest: Path) -> bool:
    if not _configure():
        return False
    import cloudinary.api
    try:
        info = cloudinary.api.resource(public_id, resource_type=resource_type)
        url = info.get("secure_url")
        if not url:
            return False
    except Exception:
        return False

    import httpx
    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120) as client:
        r = client.get(url)
        if r.status_code == 200:
            dest.write_bytes(r.content)
            return True
    return False


def _exists(public_id: str, resource_type: str) -> bool:
    if not _configure():
        return False
    import cloudinary.api
    try:
        cloudinary.api.resource(public_id, resource_type=resource_type)
        return True
    except Exception:
        return False


async def restore_db() -> bool:
    if not is_configured():
        log.info("Cloudinary no configurado — sin persistencia en la nube")
        return False

    try:
        found = await asyncio.to_thread(_exists, "layercut/db/layercut.db", "raw")
        if not found:
            log.info("No hay DB en Cloudinary — se usará la local")
            return False

        backup = LOCAL_DB_PATH.with_suffix(".db.bak")
        if LOCAL_DB_PATH.exists():
            shutil.copy2(LOCAL_DB_PATH, backup)

        ok = await asyncio.to_thread(
            _download, "layercut/db/layercut.db", "raw", LOCAL_DB_PATH
        )
        if ok:
            log.info(f"DB restaurada desde Cloudinary ({LOCAL_DB_PATH.stat().st_size} bytes)")
            return True
        else:
            if backup.exists():
                shutil.copy2(backup, LOCAL_DB_PATH)
            return False
    except Exception as e:
        log.error(f"Error restaurando DB desde Cloudinary: {e}")
        return False


async def backup_db() -> bool:
    if not is_configured() or not LOCAL_DB_PATH.exists():
        return False

    try:
        url = await asyncio.to_thread(
            _upload_raw, LOCAL_DB_PATH, "layercut/db/layercut"
        )
        if url:
            log.info(f"DB respaldada en Cloudinary ({LOCAL_DB_PATH.stat().st_size} bytes)")
            return True
        return False
    except Exception as e:
        log.error(f"Error respaldando DB en Cloudinary: {e}")
        return False


async def upload_render(project_id: str, video_path: Path) -> Optional[str]:
    if not is_configured() or not video_path.exists():
        return None

    try:
        public_id = f"layercut/projects/{project_id}/{video_path.stem}"
        url = await asyncio.to_thread(_upload_video, video_path, public_id)
        if url:
            log.info(f"Render subido a Cloudinary: {url}")
        return url
    except Exception as e:
        log.error(f"Error subiendo render a Cloudinary: {e}")
        return None


async def upload_layer_file(project_id: str, layer: str, file_path: Path) -> Optional[str]:
    if not is_configured() or not file_path.exists():
        return None

    try:
        public_id = f"layercut/projects/{project_id}/{layer}/{file_path.stem}"
        rtype = "video" if file_path.suffix in (".mp4", ".webm", ".mov") else "raw"
        if rtype == "video":
            url = await asyncio.to_thread(_upload_video, file_path, public_id)
        else:
            url = await asyncio.to_thread(_upload_raw, file_path, public_id)
        return url
    except Exception as e:
        log.error(f"Error subiendo {layer}/{file_path.name} a Cloudinary: {e}")
        return None


async def download_render(project_id: str, filename: str, dest: Path) -> bool:
    if not is_configured():
        return False

    try:
        stem = Path(filename).stem
        public_id = f"layercut/projects/{project_id}/{stem}"
        return await asyncio.to_thread(_download, public_id, "video", dest)
    except Exception as e:
        log.error(f"Error descargando render desde Cloudinary: {e}")
        return False


async def upload_layer(project_id: str, layer: str, file_path: Path) -> Optional[str]:
    """Sube el archivo de una capa (video/audio/music/subtitles/overlay) a Cloudinary."""
    if not is_configured() or layer not in LAYER_ASSETS or not file_path.exists():
        return None

    _, rtype = LAYER_ASSETS[layer]
    public_id = f"layercut/projects/{project_id}/layers/{layer}"
    try:
        if rtype == "video":
            url = await asyncio.to_thread(_upload_video, file_path, public_id)
        else:
            url = await asyncio.to_thread(_upload_raw, file_path, public_id)
        if url:
            log.info(f"Capa '{layer}' del proyecto {project_id} subida a Cloudinary")
        return url
    except Exception as e:
        log.error(f"Error subiendo capa '{layer}' de {project_id}: {e}")
        return None


async def restore_project_layers(project_id: str, project_dir: Path) -> int:
    """Descarga desde Cloudinary los archivos de capa que falten en disco local."""
    if not is_configured():
        return 0

    restored = 0
    for layer, (filename, rtype) in LAYER_ASSETS.items():
        dest = project_dir / layer / filename
        if dest.exists() and dest.stat().st_size > 0:
            continue
        base = f"layercut/projects/{project_id}/layers/{layer}"
        # Cloudinary añade la extensión del archivo fuente al public_id en raw.
        public_id = base + Path(filename).suffix if rtype == "raw" else base
        try:
            ok = await asyncio.to_thread(_download, public_id, rtype, dest)
            if ok:
                restored += 1
        except Exception as e:
            log.error(f"Error restaurando capa '{layer}' de {project_id}: {e}")

    if restored:
        log.info(f"Proyecto {project_id}: {restored} capas restauradas desde Cloudinary")
    return restored


async def restore_all_layers() -> int:
    """Al arrancar: restaura los archivos de capa de todos los proyectos en la BD."""
    if not is_configured():
        return 0

    from app.database import SessionLocal, Project
    from app.services import project_service
    projects_root = Path("projects")
    total = 0
    db = SessionLocal()
    try:
        # Los proyectos vencidos no se restauran: si no, el purgado por retención
        # se desharía en el siguiente arranque bajando otra vez de Cloudinary lo
        # que se acababa de borrar del disco.
        proyectos = db.query(Project).all()
        ids = [p.id for p in proyectos if not project_service.is_expired(p)]
        omitidos = len(proyectos) - len(ids)
    finally:
        db.close()
    if omitidos:
        log.info(f"Startup: {omitidos} proyectos vencidos, no se restauran sus capas")

    from app.services import motion_service
    for pid in ids:
        project_dir = projects_root / pid
        for subdir in ["video", "audio", "music", "subtitles", "overlay", "output"]:
            (project_dir / subdir).mkdir(parents=True, exist_ok=True)
        total += await restore_project_layers(pid, project_dir)
        total += await motion_service.restore_motion(pid)

    if total:
        log.info(f"Startup: {total} capas restauradas de {len(ids)} proyectos")
    return total


async def upload_library_asset(asset_id: str, video_path: Path, thumb_path: Optional[Path] = None) -> dict:
    """Sube un asset de la Biblioteca de vídeos a Cloudinary, en un namespace
    propio (layercut/library/...) separado del de proyectos. Se llama justo
    después de que import_from_url/trim_asset dejan el asset en status=ready
    — subida inmediata, restore perezoso (solo on-demand, no en cada
    arranque: a diferencia de las 5 capas fijas por proyecto, la biblioteca
    puede crecer sin límite)."""
    if not is_configured():
        return {}

    result = {}
    try:
        if video_path.exists():
            video_public_id = f"layercut/library/{asset_id}/video"
            url = await asyncio.to_thread(_upload_video, video_path, video_public_id)
            if url:
                result["cloud_video_public_id"] = video_public_id
        if thumb_path and thumb_path.exists():
            thumb_public_id = f"layercut/library/{asset_id}/thumb"
            url = await asyncio.to_thread(_upload_raw, thumb_path, thumb_public_id)
            if url:
                result["cloud_thumb_public_id"] = thumb_public_id
    except Exception as e:
        log.error(f"Error subiendo asset de biblioteca {asset_id} a Cloudinary: {e}")
        return result

    if result:
        from app.services import media_library_service as lib
        lib._update(asset_id, **result)
        log.info(f"Asset de biblioteca {asset_id} subido a Cloudinary")
    return result


async def restore_library_asset(asset_id: str, cloud_video_public_id: Optional[str], dest: Path) -> bool:
    """Restaura el vídeo de un asset de biblioteca desde Cloudinary — solo se
    llama on-demand (add_to_project, trim_asset o GET /video) cuando el
    archivo local ya no existe (p. ej. tras un redeploy de Railway)."""
    if not is_configured() or not cloud_video_public_id:
        return False
    try:
        ok = await asyncio.to_thread(_download, cloud_video_public_id, "video", dest)
        if ok:
            log.info(f"Asset de biblioteca {asset_id} restaurado desde Cloudinary")
        return ok
    except Exception as e:
        log.error(f"Error restaurando asset de biblioteca {asset_id}: {e}")
        return False


async def list_project_assets(project_id: str) -> list:
    if not is_configured():
        return []

    try:
        if not _configure():
            return []
        import cloudinary.api
        prefix = f"layercut/projects/{project_id}"
        assets = []
        for rtype in ["video", "raw"]:
            try:
                result = await asyncio.to_thread(
                    lambda rt=rtype: cloudinary.api.resources(
                        type="upload", resource_type=rt,
                        prefix=prefix, max_results=100
                    )
                )
                for r in result.get("resources", []):
                    assets.append({
                        "public_id": r["public_id"],
                        "url": r["secure_url"],
                        "type": r["resource_type"],
                        "size": r.get("bytes", 0),
                        "created": r.get("created_at"),
                    })
            except Exception:
                pass
        return assets
    except Exception as e:
        log.error(f"Error listando assets de proyecto {project_id}: {e}")
        return []
