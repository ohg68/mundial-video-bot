import json
import uuid
import shutil
from pathlib import Path
from datetime import datetime
from app.models.project import ProjectConfig, LayerStatus

PROJECTS_DIR = Path("projects")
PROJECTS_DIR.mkdir(exist_ok=True)

LAYER_FILES = {
    "video": "video.mp4",
    "audio": "narration.mp3",
    "music": "music.mp3",
    "subtitles": "subtitles.srt",
    "overlay": "overlay.png",
}

def create_project(config: ProjectConfig) -> dict:
    project_id = str(uuid.uuid4())[:8]
    project_dir = PROJECTS_DIR / project_id
    for subdir in ["video", "audio", "music", "subtitles", "overlay", "output"]:
        (project_dir / subdir).mkdir(parents=True, exist_ok=True)

    meta = {
        "id": project_id,
        "title": config.title,
        "topic": config.topic,
        "match": config.match,
        "match_date": config.match_date,
        "created_at": datetime.utcnow().isoformat(),
        "config": config.model_dump(),
        "layers": {
            "video": LayerStatus.empty,
            "audio": LayerStatus.empty,
            "music": LayerStatus.empty,
            "subtitles": LayerStatus.empty,
            "overlay": LayerStatus.empty,
        },
        "output": None,
    }
    _save_meta(project_dir, meta)
    return meta

def get_project(project_id: str) -> dict:
    meta_path = PROJECTS_DIR / project_id / "project.json"
    if not meta_path.exists():
        return None
    return json.loads(meta_path.read_text())

def list_projects() -> list:
    projects = []
    for d in sorted(PROJECTS_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        meta_path = d / "project.json"
        if meta_path.exists():
            projects.append(json.loads(meta_path.read_text()))
    return projects

def update_layer_status(project_id: str, layer: str, status: LayerStatus, info: dict = None):
    meta = get_project(project_id)
    if not meta:
        return None
    meta["layers"][layer] = status
    if info:
        meta.setdefault("layer_info", {})[layer] = info
    _save_meta(PROJECTS_DIR / project_id, meta)
    return meta

def replace_layer_file(project_id: str, layer: str, source_path: str) -> bool:
    project_dir = PROJECTS_DIR / project_id
    if not project_dir.exists():
        return False
    dest = project_dir / layer / LAYER_FILES[layer]
    shutil.copy2(source_path, dest)
    update_layer_status(project_id, layer, LayerStatus.ready, {"source": "custom", "file": str(dest)})
    return True

def get_layer_path(project_id: str, layer: str) -> Path:
    return PROJECTS_DIR / project_id / layer / LAYER_FILES[layer]

def _save_meta(project_dir: Path, meta: dict):
    (project_dir / "project.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
