"""Endpoints de ayuda para configurar/verificar Google Drive como fuente de video."""
from fastapi import APIRouter, HTTPException
from app.services import gdrive_service

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
