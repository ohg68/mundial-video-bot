from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from app.services import render_service, project_service

router = APIRouter()

@router.post("/{project_id}")
async def render_video(project_id: str, background_tasks: BackgroundTasks):
    project = project_service.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    background_tasks.add_task(_do_render, project_id)
    return {"status": "rendering", "project_id": project_id}

async def _do_render(project_id: str):
    try:
        output = await render_service.render_final(project_id)
        project_service.update_layer_status(project_id, "video", "ready", {
            "output": str(output)
        })
    except Exception as e:
        project_service.update_layer_status(project_id, "video", "error", {"error": str(e)})

@router.get("/{project_id}/download")
async def download_output(project_id: str):
    from pathlib import Path
    output_path = Path("projects") / project_id / "output" / "final.mp4"
    if not output_path.exists():
        raise HTTPException(status_code=404, detail="Output not ready yet")
    return FileResponse(str(output_path), filename=f"{project_id}_final.mp4")
