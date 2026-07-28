from fastapi import APIRouter, HTTPException, Body
from app.services import editing_service, project_service

router = APIRouter()


@router.post("/{project_id}/generate")
async def generate_plan(project_id: str, body: dict = Body(...)):
    meta = project_service.get_project(project_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Project not found")

    video_type = body.get("video_type", "marketing")
    editing_prompt = body.get("editing_prompt", "")
    bpm = body.get("bpm")
    provider = body.get("provider", "deepseek")
    total_duration = body.get("total_duration", 90.0)

    if not editing_prompt:
        raise HTTPException(status_code=400, detail="editing_prompt required")

    try:
        plan = await editing_service.generate_editing_plan(
            video_type=video_type,
            editing_prompt=editing_prompt,
            total_duration=total_duration,
            bpm=bpm,
            provider=provider,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM error: {str(e)}")

    config = meta.get("config", {})
    config["editing"] = {
        "video_type": video_type,
        "editing_prompt": editing_prompt,
        "bpm": bpm,
        "editing_plan": plan,
    }
    project_service.update_project_config(project_id, config)

    return {"plan": plan, "video_type": video_type}


@router.get("/{project_id}/plan")
async def get_plan(project_id: str):
    meta = project_service.get_project(project_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Project not found")
    editing = meta.get("config", {}).get("editing", {})
    return {
        "plan": editing.get("editing_plan"),
        "video_type": editing.get("video_type"),
        "editing_prompt": editing.get("editing_prompt"),
        "bpm": editing.get("bpm"),
    }


@router.patch("/{project_id}/plan")
async def update_plan(project_id: str, body: dict = Body(...)):
    meta = project_service.get_project(project_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Project not found")

    config = meta.get("config", {})
    editing = config.get("editing", {})

    if "plan" in body:
        total_dur = body["plan"].get("total_duration", 90.0)
        editing["editing_plan"] = editing_service.validate_plan(body["plan"], total_dur)
    if "video_type" in body:
        editing["video_type"] = body["video_type"]
    if "editing_prompt" in body:
        editing["editing_prompt"] = body["editing_prompt"]
    if "bpm" in body:
        editing["bpm"] = body["bpm"]

    config["editing"] = editing
    project_service.update_project_config(project_id, config)

    return {"ok": True, "editing": editing}


@router.get("/defaults/{video_type}")
async def get_defaults(video_type: str):
    if video_type not in ("marketing", "music", "sports"):
        raise HTTPException(status_code=400, detail="Invalid video_type")
    return editing_service.get_defaults(video_type)
