from fastapi import APIRouter, HTTPException, UploadFile, File, Body, BackgroundTasks
from fastapi.responses import Response
from app.services import video_sources, tts_service, llm_service, project_service
from pathlib import Path
import json
import tempfile
import shutil

router = APIRouter()


# ── Video clip search ──────────────────────────────────────────

@router.get("/clips/search")
async def search_clips(q: str, sources: str = "pexels,pixabay", count: int = 12):
    source_list = [s.strip() for s in sources.split(",")]
    clips = await video_sources.search_clips(q, source_list, count)
    return {"clips": clips, "count": len(clips)}


@router.post("/clips/download")
async def download_clip(body: dict = Body(...)):
    clip = body.get("clip")
    project_id = body.get("project_id")
    if not clip or not project_id:
        raise HTTPException(status_code=400, detail="clip and project_id required")
    dest_dir = Path("projects") / project_id / "video" / "downloads"
    path = await video_sources.download_clip(clip, dest_dir)
    if not path:
        raise HTTPException(status_code=500, detail="Failed to download clip")
    return {"path": str(path), "filename": path.name}


@router.post("/{project_id}/clips/upload")
async def upload_multiple_clips(project_id: str, files: list[UploadFile] = File(...)):
    project = project_service.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    dest_dir = Path("projects") / project_id / "video" / "uploads"
    dest_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in files:
        dest = dest_dir / f.filename
        with open(dest, "wb") as out:
            shutil.copyfileobj(f.file, out)
        saved.append({"filename": f.filename, "path": str(dest)})
    return {"uploaded": len(saved), "files": saved}


# ── TTS ────────────────────────────────────────────────────────

@router.post("/tts/preview")
async def tts_preview(body: dict = Body(...)):
    provider = body.get("provider", "edge")
    voice = body.get("voice", "es-ES-AlvaroNeural")
    voice_id = body.get("voice_id")
    text = body.get("text")
    speed = body.get("speed", 1.0)
    audio_bytes = await tts_service.generate_preview(provider, text, voice, voice_id, speed)
    return Response(content=audio_bytes, media_type="audio/mpeg")


@router.get("/tts/voices/elevenlabs")
async def list_elevenlabs_voices():
    voices = await tts_service.list_elevenlabs_voices()
    return {"voices": voices}


@router.get("/tts/voices")
async def list_all_voices():
    edge_voices = [
        {"id": "es-ES-AlvaroNeural", "name": "Alvaro", "provider": "edge", "lang": "es-ES"},
        {"id": "es-ES-ElviraNeural", "name": "Elvira", "provider": "edge", "lang": "es-ES"},
        {"id": "pt-PT-DuarteNeural", "name": "Duarte", "provider": "edge", "lang": "pt-PT"},
        {"id": "pt-PT-InesNeural", "name": "Inés", "provider": "edge", "lang": "pt-PT"},
        {"id": "en-US-GuyNeural", "name": "Guy", "provider": "edge", "lang": "en-US"},
        {"id": "en-US-JennyNeural", "name": "Jenny", "provider": "edge", "lang": "en-US"},
    ]
    openai_voices = [
        {"id": "alloy", "name": "Alloy", "provider": "openai"},
        {"id": "echo", "name": "Echo", "provider": "openai"},
        {"id": "fable", "name": "Fable", "provider": "openai"},
        {"id": "onyx", "name": "Onyx", "provider": "openai"},
        {"id": "nova", "name": "Nova", "provider": "openai"},
        {"id": "shimmer", "name": "Shimmer", "provider": "openai"},
    ]
    el_voices = await tts_service.list_elevenlabs_voices()
    for v in el_voices:
        v["provider"] = "elevenlabs"

    return {
        "edge": edge_voices,
        "openai": openai_voices,
        "elevenlabs": el_voices,
    }


# ── LLM / Script ──────────────────────────────────────────────

@router.post("/script/generate")
async def generate_script(body: dict = Body(...)):
    topic = body.get("topic")
    if not topic:
        raise HTTPException(status_code=400, detail="topic required")
    provider = body.get("provider", "deepseek")
    template = body.get("template", "free")
    language = body.get("language", "es")
    match = body.get("match")
    match_date = body.get("match_date")

    script = await llm_service.generate_script(
        topic, provider, template, language, match, match_date,
    )
    timestamps = llm_service.estimate_timestamps(script)
    return {"script": script, "timestamps": timestamps, "provider": provider, "template": template}


@router.post("/script/timestamps")
async def get_timestamps(body: dict = Body(...)):
    script = body.get("script", "")
    wpm = body.get("wpm", 150)
    return {"timestamps": llm_service.estimate_timestamps(script, wpm)}


@router.get("/script/templates")
async def list_templates():
    return {"templates": llm_service.get_templates()}
