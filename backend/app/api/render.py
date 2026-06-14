import json
import asyncio
import shutil
from pathlib import Path
from datetime import datetime
from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from app.services import render_service, project_service
from app.websocket import manager, parse_ffmpeg_progress

router = APIRouter()
PROJECTS_DIR = Path("projects")


@router.post("/{project_id}")
async def render_video(project_id: str, background_tasks: BackgroundTasks):
    project = project_service.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    background_tasks.add_task(_do_render, project_id, quality="full")
    return {"status": "rendering", "project_id": project_id, "quality": "full"}


@router.post("/{project_id}/quick")
async def render_quick(project_id: str, background_tasks: BackgroundTasks):
    project = project_service.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    background_tasks.add_task(_do_render, project_id, quality="quick")
    return {"status": "rendering", "project_id": project_id, "quality": "quick"}


async def _do_render(project_id: str, quality: str = "full"):
    try:
        await manager.send_progress(project_id, {
            "type": "task_started", "task_type": "render", "quality": quality,
        })
        output = await render_service.render_final(project_id, quality=quality)

        # Save to history
        _save_to_history(project_id, output, quality)

        project_service.update_layer_status(project_id, "video", "ready", {
            "output": str(output)
        })
        await manager.send_progress(project_id, {
            "type": "task_completed", "task_type": "render", "progress": 100,
        })
    except Exception as e:
        project_service.update_layer_status(project_id, "video", "error", {"error": str(e)})
        await manager.send_progress(project_id, {
            "type": "task_failed", "task_type": "render", "error": str(e),
        })


def _save_to_history(project_id: str, output_path: Path, quality: str):
    history_dir = PROJECTS_DIR / project_id / "output" / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    suffix = "_quick" if quality == "quick" else ""
    dest = history_dir / f"render_{timestamp}{suffix}.mp4"
    shutil.copy2(output_path, dest)

    history_file = history_dir / "index.json"
    history = []
    if history_file.exists():
        history = json.loads(history_file.read_text())

    size = dest.stat().st_size
    history.insert(0, {
        "filename": dest.name,
        "quality": quality,
        "size_bytes": size,
        "size_mb": round(size / 1024 / 1024, 1),
        "created_at": datetime.utcnow().isoformat(),
    })
    history_file.write_text(json.dumps(history, indent=2, ensure_ascii=False))


@router.get("/{project_id}/download")
async def download_output(project_id: str):
    output_path = PROJECTS_DIR / project_id / "output" / "final.mp4"
    if not output_path.exists():
        raise HTTPException(status_code=404, detail="Output not ready yet")
    return FileResponse(str(output_path), filename=f"{project_id}_final.mp4")


@router.get("/{project_id}/history")
async def render_history(project_id: str):
    history_file = PROJECTS_DIR / project_id / "output" / "history" / "index.json"
    if not history_file.exists():
        return {"history": []}
    return {"history": json.loads(history_file.read_text())}


@router.get("/{project_id}/history/{filename}")
async def download_history_render(project_id: str, filename: str):
    path = PROJECTS_DIR / project_id / "output" / "history" / filename
    if not path.exists() or ".." in filename:
        raise HTTPException(status_code=404, detail="Render not found")
    return FileResponse(str(path), filename=filename)


@router.delete("/{project_id}/history/{filename}")
async def delete_history_render(project_id: str, filename: str):
    path = PROJECTS_DIR / project_id / "output" / "history" / filename
    if not path.exists() or ".." in filename:
        raise HTTPException(status_code=404, detail="Render not found")

    freed = path.stat().st_size
    path.unlink()

    history_file = path.parent / "index.json"
    if history_file.exists():
        history = json.loads(history_file.read_text())
        history = [h for h in history if h["filename"] != filename]
        history_file.write_text(json.dumps(history, indent=2, ensure_ascii=False))

    return {"deleted": filename, "freed_bytes": freed}


@router.get("/{project_id}/durations")
async def get_layer_durations(project_id: str):
    layers = {}
    for layer_key, layer_file in project_service.LAYER_FILES.items():
        path = PROJECTS_DIR / project_id / layer_key / layer_file
        if path.exists():
            dur = await _ffprobe_duration(path)
            layers[layer_key] = {
                "duration": dur,
                "exists": True,
                "size_bytes": path.stat().st_size,
            }
        else:
            layers[layer_key] = {"duration": 0, "exists": False, "size_bytes": 0}

    output_path = PROJECTS_DIR / project_id / "output" / "final.mp4"
    if output_path.exists():
        layers["output"] = {
            "duration": await _ffprobe_duration(output_path),
            "exists": True,
            "size_bytes": output_path.stat().st_size,
        }

    return {"layers": layers}


async def _ffprobe_duration(path: Path) -> float:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return round(float(stdout.decode().strip()), 1)
    except Exception:
        return 0
