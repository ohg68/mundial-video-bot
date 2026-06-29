from fastapi import APIRouter, HTTPException, UploadFile, File, BackgroundTasks
from fastapi.responses import FileResponse
from app.models.project import ProjectConfig, LayerUpdate
from app.services import project_service, layer_service, tts_service, llm_service
import tempfile, shutil, json
from pathlib import Path

router = APIRouter()

@router.post("/{project_id}/generate/{layer}")
async def generate_layer(project_id: str, layer: str, background_tasks: BackgroundTasks):
    project = project_service.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    config = ProjectConfig(**project["config"])

    if layer == "script":
        provider = config.llm_provider
        template = config.script_template
        script = await llm_service.generate_script(
            config.topic, provider, template, config.language,
            config.match, config.match_date,
        )
        # Persistir en SQLite (project.json es efímero en Railway)
        project_service.update_project_config(project_id, {"script": script})
        timestamps = llm_service.estimate_timestamps(script)
        return {"script": script, "timestamps": timestamps, "provider": provider}

    elif layer == "audio":
        background_tasks.add_task(_generate_audio_task, project_id, config)
        return {"status": "generating", "layer": "audio"}

    elif layer == "video":
        background_tasks.add_task(layer_service.assemble_video_layer, project_id, config)
        return {"status": "generating", "layer": "video"}

    elif layer == "subtitles":
        background_tasks.add_task(layer_service.generate_subtitles, project_id)
        return {"status": "generating", "layer": "subtitles"}

    else:
        raise HTTPException(status_code=400, detail=f"Cannot auto-generate layer: {layer}")


async def _generate_audio_task(project_id: str, config: ProjectConfig):
    from app.models.project import LayerStatus
    project_service.update_layer_status(project_id, "audio", LayerStatus.pending)
    script = config.script
    if not script:
        script = await llm_service.generate_script(
            config.topic, config.llm_provider, config.script_template,
            config.language, config.match, config.match_date,
        )
    output_path = project_service.get_layer_path(project_id, "audio")
    provider = config.audio.tts_provider
    voice = config.audio.voice.value if config.audio.voice.value != "custom" else "es-ES-AlvaroNeural"
    voice_id = config.audio.elevenlabs_voice_id
    if provider == "openai":
        voice = config.audio.openai_voice

    await tts_service.generate_full(provider, script, output_path, voice, voice_id, config.audio.speed)
    project_service.update_layer_status(project_id, "audio", LayerStatus.ready, {
        "voice": voice,
        "provider": provider,
        "file": str(output_path),
    })


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

    merged = {**project["config"].get(layer, {}), **update}
    # Persistir en SQLite (project.json es efímero en Railway)
    project_service.update_project_config(project_id, {layer: merged})
    return {"updated": layer, "config": merged}

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
    # Persistir en SQLite (no en project.json, que es efímero en Railway)
    project_service.update_project_config(project_id, {"script": body.get("script", "")})
    return {"updated": "script"}
