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
