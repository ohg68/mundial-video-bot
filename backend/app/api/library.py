import mimetypes
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Body, Request
from fastapi.responses import FileResponse, StreamingResponse

from app.services import media_library_service as lib

router = APIRouter()


def _range_response(path: Path, request: Request):
    """Sirve un archivo con soporte de HTTP Range — necesario para que un
    <video> pueda buscar/adelantar en el reproductor de recorte."""
    size = path.stat().st_size
    ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    range_header = request.headers.get("range")

    start, end = 0, size - 1
    status = 200
    if range_header and range_header.startswith("bytes="):
        status = 206
        rng = range_header[6:].split("-")
        start = int(rng[0]) if rng[0] else 0
        end = int(rng[1]) if len(rng) > 1 and rng[1] else size - 1
        end = min(end, size - 1)
    length = end - start + 1

    def _iter():
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                yield chunk
                remaining -= len(chunk)

    headers = {"Accept-Ranges": "bytes", "Content-Length": str(length)}
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(_iter(), status_code=status, media_type=ctype, headers=headers)


@router.post("/")
async def import_media(background_tasks: BackgroundTasks, body: dict = Body(...)):
    url = (body.get("url") or "").strip()
    if not url or not url.startswith(("http://", "https://")):
        raise HTTPException(400, "URL inválida")
    asset = lib.create_pending_asset(url)
    background_tasks.add_task(lib.import_from_url, asset["id"], url)
    return asset


@router.get("/")
async def list_media(status: str = None, q: str = None, limit: int = 50, offset: int = 0):
    return lib.list_assets(status=status, q=q, limit=limit, offset=offset)


@router.get("/{asset_id}")
async def get_media(asset_id: str):
    asset = lib.get_asset(asset_id)
    if not asset:
        raise HTTPException(404, "Asset no encontrado")
    return asset


@router.post("/{asset_id}/trim")
async def trim_media(asset_id: str, background_tasks: BackgroundTasks, body: dict = Body(...)):
    parent = lib.get_asset(asset_id)
    if not parent:
        raise HTTPException(404, "Asset no encontrado")
    if parent["status"] != "ready":
        raise HTTPException(409, "El asset todavía no está listo")
    try:
        start = float(body.get("start"))
        end = float(body.get("end"))
    except (TypeError, ValueError):
        raise HTTPException(400, "start/end deben ser números")
    if start < 0 or end <= start or (parent["duration"] and end > parent["duration"] + 0.5):
        raise HTTPException(400, "Rango de recorte inválido")
    child = lib.create_trim_child(parent, start, end)
    background_tasks.add_task(lib.trim_asset, child["id"], asset_id, start, end)
    return child


@router.post("/{asset_id}/add-to-project")
async def add_to_project(asset_id: str, body: dict = Body(...)):
    project_id = (body.get("project_id") or "").strip()
    if not project_id:
        raise HTTPException(400, "Falta project_id")
    from app.services import project_service
    if not project_service.get_project(project_id):
        raise HTTPException(404, "Proyecto no encontrado")
    try:
        dest = await lib.add_to_project(asset_id, project_id)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(409, str(e))
    return {"status": "added", "filename": dest.name, "project_id": project_id}


@router.get("/{asset_id}/video")
async def get_video(asset_id: str, request: Request):
    asset = lib.get_asset(asset_id)
    if not asset:
        raise HTTPException(404, "Asset no encontrado")
    path = Path(asset["file_path"]) if asset.get("file_path") else None
    if not path or not path.exists():
        if asset.get("status") == "ready" and asset.get("cloud_video_public_id"):
            try:
                from app.services import cloud_storage
                dest = lib._video_path(asset_id)
                if await cloud_storage.restore_library_asset(asset_id, asset["cloud_video_public_id"], dest):
                    path = dest
            except Exception:
                path = None
        if not path or not path.exists():
            raise HTTPException(404, "El archivo no está disponible")
    return _range_response(path, request)


@router.get("/{asset_id}/thumbnail")
async def get_thumbnail(asset_id: str):
    asset = lib.get_asset(asset_id)
    if not asset or not asset.get("thumbnail_path") or not Path(asset["thumbnail_path"]).exists():
        raise HTTPException(404, "Sin thumbnail")
    return FileResponse(asset["thumbnail_path"], media_type="image/jpeg")


@router.delete("/{asset_id}")
async def delete_media(asset_id: str):
    if not lib.delete_asset(asset_id):
        raise HTTPException(404, "Asset no encontrado")
    return {"status": "deleted", "id": asset_id}
