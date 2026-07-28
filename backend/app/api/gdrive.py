"""Endpoints de ayuda para configurar/verificar Google Drive como fuente de video y cloud storage."""
from fastapi import APIRouter, HTTPException
from app.services import gdrive_service, cloud_storage

router = APIRouter()


@router.get("/status")
def status():
    """¿Está configurada la cuenta de servicio? ¿Con qué email hay que compartir la carpeta?"""
    if not gdrive_service.is_configured():
        return {"configured": False, "email": None,
                "hint": "Falta GOOGLE_SERVICE_ACCOUNT_JSON en el entorno."}
    return {
        "configured": True,
        "email": gdrive_service.service_account_email(),
        "hint": "Comparte tu carpeta de videos en Drive con este email (solo lectura).",
    }


@router.get("/folders")
def folders():
    """Carpetas de Drive visibles para la cuenta de servicio (las que le compartiste)."""
    if not gdrive_service.is_configured():
        raise HTTPException(status_code=400, detail="GOOGLE_SERVICE_ACCOUNT_JSON no configurado")
    items = gdrive_service.list_folders()
    return {"count": len(items), "folders": items}


@router.get("/folders/{folder_id}/videos")
def folder_videos(folder_id: str):
    """Videos dentro de una carpeta (para confirmar que la comparte bien)."""
    if not gdrive_service.is_configured():
        raise HTTPException(status_code=400, detail="GOOGLE_SERVICE_ACCOUNT_JSON no configurado")
    vids = gdrive_service.list_videos(folder_id)
    return {"count": len(vids), "videos": [{"id": v["id"], "name": v.get("name")} for v in vids]}


@router.get("/storage/status")
async def storage_status():
    return {
        "configured": cloud_storage.is_configured(),
        "provider": "cloudinary",
    }


@router.post("/storage/backup")
async def force_backup():
    ok = await cloud_storage.backup_db()
    return {"success": ok}


@router.post("/storage/restore")
async def force_restore():
    ok = await cloud_storage.restore_db()
    return {"success": ok}


@router.post("/storage/upload/{project_id}")
async def upload_project(project_id: str):
    from pathlib import Path
    project_dir = Path("projects") / project_id
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project directory not found")
    count = 0
    for subdir in ["video", "audio", "music", "subtitles", "output"]:
        sub_path = project_dir / subdir
        if not sub_path.exists():
            continue
        for f in sub_path.iterdir():
            if f.is_file() and f.stat().st_size > 0:
                await cloud_storage.upload_layer_file(project_id, subdir, f)
                count += 1
    await cloud_storage.backup_db()
    return {"uploaded_files": count}


@router.get("/storage/assets/{project_id}")
async def list_assets(project_id: str):
    assets = await cloud_storage.list_project_assets(project_id)
    return {"count": len(assets), "assets": assets}
