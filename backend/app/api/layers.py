from fastapi import APIRouter, HTTPException, UploadFile, File, BackgroundTasks
from fastapi.responses import FileResponse
from app.models.project import ProjectConfig, LayerUpdate, LayerStatus
from app.services import project_service, layer_service, render_service
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
        background_tasks.add_task(layer_service.generate_subtitles, project_id, config)
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
    import json
    (Path("projects") / project_id / "project.json").write_text(
        json.dumps(project, indent=2, ensure_ascii=False)
    )
    return {"updated": "script"}


# --- Estado en memoria del pipeline -----------------------------------------
_pipeline_state: dict[str, dict] = {}


def _set_pipeline(project_id: str, step: str, status: str, error: str | None = None):
    _pipeline_state[project_id] = {
        "step": step,
        "status": status,
        "error": error,
    }
    try:
        meta = project_service.get_project(project_id)
        if meta is not None:
            meta["pipeline"] = _pipeline_state[project_id]
            import json
            pdir = Path("projects") / project_id
            (pdir / "project.json").write_text(
                json.dumps(meta, indent=2, ensure_ascii=False)
            )
    except Exception:
        pass


async def _run_pipeline(project_id: str):
    """Ejecuta el pipeline completo en orden garantizado."""
    meta = project_service.get_project(project_id)
    if not meta:
        _set_pipeline(project_id, "audio", "error", "Proyecto no encontrado")
        return

    config = ProjectConfig(**meta["config"])

    try:
        # 1. Audio + subtitulos (edge-tts los sincroniza en una sola llamada).
        _set_pipeline(project_id, "audio", "running")
        await layer_service.generate_audio(project_id, config)

        # 2. Capa de video (descarga/normaliza clips y concatena).
        _set_pipeline(project_id, "video", "running")
        await layer_service.assemble_video_layer(project_id, config)

        # 3. Render final (compone todo + loudnorm).
        _set_pipeline(project_id, "render", "running")
        await render_service.render_final(project_id)

        _set_pipeline(project_id, "done", "ok")

    except Exception as e:
        last = _pipeline_state.get(project_id, {}).get("step", "audio")
        _set_pipeline(project_id, last, "error", str(e))


# --- ENDPOINTS ---------------------------------------------------------------

@router.post("/{project_id}/generate-all")
async def generate_all(project_id: str, background_tasks: BackgroundTasks):
    """Genera todas las capas en orden garantizado y renderiza el video final."""
    if not project_service.get_project(project_id):
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")

    current = _pipeline_state.get(project_id)
    if current and current["status"] == "running":
        return {
            "status": "already_running",
            "step": current["step"],
            "message": "El pipeline ya esta en ejecucion para este proyecto.",
        }

    _set_pipeline(project_id, "audio", "running")
    background_tasks.add_task(_run_pipeline, project_id)
    return {"status": "pipeline_started", "project_id": project_id}


@router.get("/{project_id}/generate-all/status")
async def generate_all_status(project_id: str):
    """Consulta el estado del pipeline (para que el frontend haga polling)."""
    state = _pipeline_state.get(project_id)
    if state is None:
        meta = project_service.get_project(project_id)
        state = (meta or {}).get("pipeline")
    if state is None:
        return {"status": "not_started"}
    return state
