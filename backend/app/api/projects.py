from fastapi import APIRouter, HTTPException, Body, Depends, Query
from typing import Optional
from app.models.project import ProjectConfig
from app.services import project_service
from app.auth import get_current_user, get_optional_user

router = APIRouter()


@router.get("/")
def list_projects(
    category: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    user=Depends(get_optional_user),
):
    # Sin filtrar por dueño a propósito: el sistema es de una sola persona y ya
    # hay que estar autenticado para llegar hasta acá. Filtrando, los proyectos
    # anteriores al seguimiento de dueño (owner_id nulo) desaparecerían de la web
    # sin haberse borrado.
    return project_service.list_projects(owner_id=None, category=category, tag=tag)


@router.post("/")
async def create_project(config: ProjectConfig, user=Depends(get_optional_user)):
    # El dueño es el chat de Telegram de la sesión, para que _owns() del bot siga
    # reconociendo como propios los proyectos creados desde la web. Con la llave
    # de emergencia no hay chat, y queda sin dueño como los antiguos.
    owner_id = user.get("chat_id") if user else None
    result = project_service.create_project(config, owner_id=owner_id)
    from app.services import cloud_storage
    await cloud_storage.backup_db()
    return result


@router.get("/stats")
def get_stats():
    return project_service.get_all_stats()


@router.get("/retention")
def retention_status():
    """Qué proyectos están vencidos y cuánto disco liberaría purgarlos ahora."""
    return project_service.retention_status()


@router.post("/purge-expired")
def purge_expired(days: int = None):
    """Ejecuta el purgado por retención a demanda (además del automático)."""
    return project_service.purge_expired_files(days)


@router.post("/bulk-delete")
def bulk_delete(body: dict = Body(...), user=Depends(get_current_user)):
    ids = body.get("project_ids", [])
    if not ids:
        raise HTTPException(status_code=400, detail="project_ids required")
    return project_service.bulk_delete(ids)


@router.get("/{project_id}/size")
def get_project_size(project_id: str):
    return project_service.get_project_size(project_id)


@router.post("/{project_id}/duplicate")
def duplicate_project(project_id: str):
    return project_service.duplicate_project(project_id)


@router.delete("/{project_id}/renders")
def clear_renders(project_id: str):
    return project_service.clear_renders(project_id)


@router.patch("/{project_id}/tags")
def update_tags(project_id: str, body: dict = Body(...)):
    tags = body.get("tags", [])
    result = project_service.update_tags(project_id, tags)
    if not result:
        raise HTTPException(status_code=404, detail="Project not found")
    return result


@router.patch("/{project_id}/category")
def update_category(project_id: str, body: dict = Body(...)):
    category = body.get("category", "")
    result = project_service.update_category(project_id, category)
    if not result:
        raise HTTPException(status_code=404, detail="Project not found")
    return result


@router.get("/{project_id}")
def get_project(project_id: str):
    project = project_service.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.delete("/{project_id}")
def delete_project(project_id: str):
    success = project_service.delete_project(project_id)
    if not success:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"deleted": project_id}
