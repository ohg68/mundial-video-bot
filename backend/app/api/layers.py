from fastapi import APIRouter, HTTPException, UploadFile, File, BackgroundTasks
from fastapi.responses import FileResponse
from app.models.project import ProjectConfig, LayerUpdate, LayerStatus
from app.services import project_service, layer_service, render_service, tts_service, llm_service, sfx_service
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
        background_tasks.add_task(layer_service.generate_subtitles, project_id, config)
        return {"status": "generating", "layer": "subtitles"}

    else:
        raise HTTPException(status_code=400, detail=f"Cannot auto-generate layer: {layer}")


async def _generate_audio_task(project_id: str, config: ProjectConfig):
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


@router.post("/{project_id}/sfx")
async def generate_sfx(project_id: str, body: dict):
    """Genera un foley con ElevenLabs y lo guarda en el proyecto.

    Body: {"prompt": "referee whistle blowing", "duration": 2}  (duration opcional, 0.5-22s)
    El prompt funciona mejor en inglés.
    """
    if not project_service.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")

    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Falta 'prompt' con la descripción del sonido")

    duration = body.get("duration")
    if duration is not None and not 0.5 <= float(duration) <= 22:
        raise HTTPException(status_code=400, detail="duration debe estar entre 0.5 y 22 segundos")

    try:
        dest = await sfx_service.generate_sfx(project_id, prompt, duration)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {"status": "generated", "project_id": project_id, "file": dest.name}


@router.get("/{project_id}/sfx")
async def list_sfx(project_id: str):
    """Lista los foleys generados del proyecto."""
    d = sfx_service.sfx_dir(project_id)
    if not d.exists():
        return {"sfx": [], "total": 0}
    names = [p.name for p in sorted(d.glob("*.mp3"))]
    return {"sfx": names, "total": len(names)}


@router.get("/{project_id}/sfx/{filename}")
async def download_sfx(project_id: str, filename: str):
    """Descarga un foley del proyecto."""
    path = sfx_service.sfx_dir(project_id) / Path(filename).name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Foley no encontrado")
    return FileResponse(str(path), filename=path.name)


@router.delete("/{project_id}/sfx")
async def clear_sfx(project_id: str):
    """Borra todos los foleys del proyecto."""
    d = sfx_service.sfx_dir(project_id)
    if d.exists():
        for p in d.glob("*.mp3"):
            p.unlink(missing_ok=True)
    return {"status": "cleared", "project_id": project_id}


@router.post("/{project_id}/upload-clips")
async def upload_clips(project_id: str, files: list[UploadFile] = File(...)):
    """Sube uno o varios clips de vídeo propios al proyecto.

    Estos clips reemplazan a Pexels: al generar el vídeo se usan estos,
    cortados por escena según la duración del audio.
    """
    project = project_service.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    clips_dir = Path("projects") / project_id / "video" / "user_clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    saved = []
    allowed = {".mp4", ".mov", ".webm", ".mkv"}
    for f in files:
        ext = Path(f.filename).suffix.lower()
        if ext not in allowed:
            continue
        dest = clips_dir / f.filename
        with open(dest, "wb") as out:
            shutil.copyfileobj(f.file, out)
        saved.append(f.filename)

    if not saved:
        raise HTTPException(
            status_code=400,
            detail="No se subió ningún clip válido (formatos: mp4, mov, webm, mkv)",
        )

    return {
        "status": "uploaded",
        "project_id": project_id,
        "clips": saved,
        "total": len(list(clips_dir.glob("*"))),
    }


@router.get("/{project_id}/clips")
async def list_clips(project_id: str):
    """Lista los clips propios subidos al proyecto."""
    clips_dir = Path("projects") / project_id / "video" / "user_clips"
    if not clips_dir.exists():
        return {"clips": [], "total": 0}
    names = [p.name for p in sorted(clips_dir.glob("*")) if p.is_file()]
    return {"clips": names, "total": len(names)}


@router.delete("/{project_id}/clips")
async def clear_clips(project_id: str):
    """Borra todos los clips propios del proyecto (para empezar de cero)."""
    clips_dir = Path("projects") / project_id / "video" / "user_clips"
    if clips_dir.exists():
        for p in clips_dir.glob("*"):
            if p.is_file():
                p.unlink(missing_ok=True)
    return {"status": "cleared", "project_id": project_id}


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
        # 1. Audio (TTS) y subtitulos sincronizados con el audio (Whisper con
        #    fallback a reparto calculado).
        _set_pipeline(project_id, "audio", "running")
        await layer_service.generate_audio(project_id, config)
        try:
            await layer_service.generate_subtitles(project_id, config)
        except Exception:
            pass  # sin subtitulos no se cae el pipeline; el render los omite

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
