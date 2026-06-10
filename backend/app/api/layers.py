from fastapi import APIRouter, HTTPException, UploadFile, File, BackgroundTasks
from fastapi.responses import FileResponse
from app.models.project import ProjectConfig, LayerUpdate
from app.services import project_service, layer_service
import tempfile, shutil
from pathlib import Path

router = APIRouter()

@router.post("/{project_id}/generate/{layer}")
async def generate_layer(project_id: str, layer: str, background_tasks: BackgroundTasks):
    project = project_service.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    config = ProjectConfig(**project["config"])

    if layer == "script":
        script = await layer_service.generate_script(project_id, config)
        return {"script": script}

    elif layer == "audio":
        background_tasks.add_task(layer_service.generate_audio, project_id, config)
        return {"status": "generating", "layer": "audio"}

    elif layer == "video":
        background_tasks.add_task(layer_service.assemble_video_layer, project_id, config)
        return {"status": "generating", "layer": "video"}

    elif layer == "subtitles":
        background_tasks.add_task(layer_service.generate_subtitles, project_id)
        return {"status": "generating", "layer": "subtitles"}

    else:
        raise HTTPException(status_code=400, detail=f"Cannot auto-generate layer: {layer}")

@router.post("/{project_id}/replace/{layer}")
async def replace_layer(project_id: str, layer: str, file: UploadFile = File(...)):
    project = project_service.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    valid_layers = ["video", "audio", "music", "subtitles", "overlay"]
    if layer not in valid_layers:
        raise HTTPException(status_code=400, detail=f"Invalid layer. Must be one of: {valid_layers}")

    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(file.filename).suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    success = project_service.replace_layer_file(project_id, layer, tmp_path)
    Path(tmp_path).unlink(missing_ok=True)

    if not success:
        raise HTTPException(status_code=500, detail="Failed to replace layer file")

    return {
        "status": "replaced",
        "layer": layer,
        "filename": file.filename,
        "project_id": project_id,
    }

@router.patch("/{project_id}/config/{layer}")
async def update_layer_config(project_id: str, layer: str, update: dict):
    project = project_service.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    project["config"][layer] = {**project["config"].get(layer, {}), **update}
    from pathlib import Path
    import json
    project_dir = Path("projects") / project_id
    (project_dir / "project.json").write_text(json.dumps(project, indent=2, ensure_ascii=False))
    return {"updated": layer, "config": project["config"][layer]}

@router.get("/{project_id}/download/{layer}")
async def download_layer(project_id: str, layer: str):
    layer_path = project_service.get_layer_path(project_id, layer)
    if not layer_path.exists():
        raise HTTPException(status_code=404, detail="Layer file not found")
    return FileResponse(str(layer_path), filename=layer_path.name)

@router.patch("/{project_id}/script")
async def update_script(project_id: str, body: dict):
    project = project_service.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    project["config"]["script"] = body.get("script", "")
    from pathlib import Path
    import json
    (Path("projects") / project_id / "project.json").write_text(
        json.dumps(project, indent=2, ensure_ascii=False)
    )
    return {"updated": "script"}
