from fastapi import APIRouter, HTTPException, Body
from app.models.project import ProjectConfig
from app.services import project_service

router = APIRouter()

@router.get("/")
def list_projects():
    return project_service.list_projects()

@router.post("/")
def create_project(config: ProjectConfig):
    return project_service.create_project(config)

@router.get("/stats")
def get_stats():
    return project_service.get_all_stats()

@router.post("/bulk-delete")
def bulk_delete(body: dict = Body(...)):
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

@router.get("/{project_id}")
def get_project(project_id: str):
    project = project_service.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project

@router.delete("/{project_id}")
def delete_project(project_id: str):
    import shutil
    from pathlib import Path
    project_dir = Path("projects") / project_id
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project not found")
    shutil.rmtree(project_dir)
    return {"deleted": project_id}
