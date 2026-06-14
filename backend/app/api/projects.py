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
    owner_id = user["user_id"] if user else None
    return project_service.list_projects(owner_id=owner_id, category=category, tag=tag)


@router.post("/")
def create_project(config: ProjectConfig, user=Depends(get_optional_user)):
    owner_id = user["user_id"] if user else None
    return project_service.create_project(config, owner_id=owner_id)


@router.get("/stats")
def get_stats():
    return project_service.get_all_stats()


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
