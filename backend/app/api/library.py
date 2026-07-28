from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Body
from fastapi.responses import FileResponse

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
