from fastapi import APIRouter, HTTPException
from app.models.project import ProjectConfig
from app.services import project_service

router = APIRouter()

@router.get("/")
def list_projects():
    return project_service.list_projects()

@router.post("/")
def create_project(config: ProjectConfig):
    return project_service.create_project(config)

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
