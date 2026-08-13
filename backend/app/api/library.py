from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Body, Request
from fastapi.responses import FileResponse

from app.media_http import serve_media
from app.services import media_library_service as lib

router = APIRouter()


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
    return serve_media(path, request)


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
