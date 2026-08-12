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
    # Dos voces (una de cada género) por cada idioma que el bot ofrece en /nuevo,
    # más las variantes de español de América, que son las que más se piden.
    # `region` es lo que agrupa el selector de la web.
    edge_voices = [
        {"id": "es-ES-AlvaroNeural", "name": "Álvaro", "provider": "edge", "lang": "es-ES", "region": "Español · España"},
        {"id": "es-ES-ElviraNeural", "name": "Elvira", "provider": "edge", "lang": "es-ES", "region": "Español · España"},
        {"id": "es-MX-JorgeNeural", "name": "Jorge", "provider": "edge", "lang": "es-MX", "region": "Español · México"},
        {"id": "es-MX-DaliaNeural", "name": "Dalia", "provider": "edge", "lang": "es-MX", "region": "Español · México"},
        {"id": "es-AR-TomasNeural", "name": "Tomás", "provider": "edge", "lang": "es-AR", "region": "Español · Argentina"},
        {"id": "es-AR-ElenaNeural", "name": "Elena", "provider": "edge", "lang": "es-AR", "region": "Español · Argentina"},
        {"id": "es-CO-GonzaloNeural", "name": "Gonzalo", "provider": "edge", "lang": "es-CO", "region": "Español · Colombia"},
        {"id": "es-CO-SalomeNeural", "name": "Salomé", "provider": "edge", "lang": "es-CO", "region": "Español · Colombia"},
        {"id": "en-US-GuyNeural", "name": "Guy", "provider": "edge", "lang": "en-US", "region": "English · US"},
        {"id": "en-US-JennyNeural", "name": "Jenny", "provider": "edge", "lang": "en-US", "region": "English · US"},
        {"id": "en-GB-RyanNeural", "name": "Ryan", "provider": "edge", "lang": "en-GB", "region": "English · UK"},
        {"id": "en-GB-SoniaNeural", "name": "Sonia", "provider": "edge", "lang": "en-GB", "region": "English · UK"},
        {"id": "pt-BR-AntonioNeural", "name": "Antônio", "provider": "edge", "lang": "pt-BR", "region": "Português · Brasil"},
        {"id": "pt-BR-FranciscaNeural", "name": "Francisca", "provider": "edge", "lang": "pt-BR", "region": "Português · Brasil"},
        {"id": "pt-PT-DuarteNeural", "name": "Duarte", "provider": "edge", "lang": "pt-PT", "region": "Português · Portugal"},
        {"id": "pt-PT-RaquelNeural", "name": "Raquel", "provider": "edge", "lang": "pt-PT", "region": "Português · Portugal"},
        {"id": "fr-FR-HenriNeural", "name": "Henri", "provider": "edge", "lang": "fr-FR", "region": "Français"},
        {"id": "fr-FR-DeniseNeural", "name": "Denise", "provider": "edge", "lang": "fr-FR", "region": "Français"},
        {"id": "de-DE-ConradNeural", "name": "Conrad", "provider": "edge", "lang": "de-DE", "region": "Deutsch"},
        {"id": "de-DE-KatjaNeural", "name": "Katja", "provider": "edge", "lang": "de-DE", "region": "Deutsch"},
        {"id": "it-IT-DiegoNeural", "name": "Diego", "provider": "edge", "lang": "it-IT", "region": "Italiano"},
        {"id": "it-IT-ElsaNeural", "name": "Elsa", "provider": "edge", "lang": "it-IT", "region": "Italiano"},
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

    target_seconds = body.get("target_seconds", 60)
    if target_seconds not in (30, 60, 90):
        raise HTTPException(status_code=400, detail="target_seconds debe ser 30, 60 o 90")

    try:
        script = await llm_service.generate_script(
            topic, provider, template, language, match, match_date, target_seconds,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM error ({provider}): {str(e)}")
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
