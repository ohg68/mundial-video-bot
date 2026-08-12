"""Intros/outros animados (HyperFrames)."""
from fastapi import APIRouter, HTTPException, Body
from fastapi.responses import FileResponse
from app.services import motion_service, project_service

router = APIRouter()


@router.get("/status")
async def service_status():
    # `available` es lo que la interfaz debe mirar para ofrecer o no la función;
    # `configured` se mantiene para distinguir "no lo configuraste" de "lo
    # configuraste pero el servicio no está".
    return {
        "configured": motion_service.is_configured(),
        "available": await motion_service.is_available(),
    }


@router.get("/{project_id}")
async def project_motion(project_id: str):
    if not project_service.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    out = {}
    for kind in motion_service.MOTION_KINDS:
        p = motion_service.motion_path(project_id, kind)
        out[kind] = {
            "exists": p.exists() and p.stat().st_size > 0,
            "size_bytes": p.stat().st_size if p.exists() else 0,
        }
    return out


@router.post("/{project_id}/{kind}")
async def generate(project_id: str, kind: str, body: dict = Body(default={})):
    if kind not in motion_service.MOTION_KINDS:
        raise HTTPException(status_code=400, detail="kind debe ser intro, outro o captions")
    if not project_service.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    if not motion_service.is_configured():
        raise HTTPException(status_code=503, detail="HYPERFRAMES_URL no configurado")

    try:
        if kind == "captions":
            accent = body.get("variables", {}).get("accentColor", "#f5c518")
            dest = await motion_service.generate_captions(project_id, accent)
        else:
            dest = await motion_service.generate_motion(
                project_id, kind, body.get("variables", {}))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {"status": "generated", "kind": kind, "size_bytes": dest.stat().st_size}


@router.get("/{project_id}/{kind}/preview")
async def preview(project_id: str, kind: str):
    if kind not in motion_service.MOTION_KINDS:
        raise HTTPException(status_code=400, detail="kind debe ser intro, outro o captions")
    p = motion_service.motion_path(project_id, kind)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"No hay {kind} generada")
    media = "video/webm" if p.suffix == ".webm" else "video/mp4"
    return FileResponse(str(p), media_type=media, filename=p.name)


@router.delete("/{project_id}/{kind}")
async def remove(project_id: str, kind: str):
    if kind not in motion_service.MOTION_KINDS:
        raise HTTPException(status_code=400, detail="kind debe ser intro, outro o captions")
    p = motion_service.motion_path(project_id, kind)
    p.unlink(missing_ok=True)
    return {"status": "deleted", "kind": kind}
