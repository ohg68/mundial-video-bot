import json
import uuid
import shutil
from pathlib import Path
from datetime import datetime
from app.models.project import ProjectConfig, LayerStatus
from app.database import SessionLocal, Project

PROJECTS_DIR = Path("projects")
PROJECTS_DIR.mkdir(exist_ok=True)

LAYER_FILES = {
    "video": "video.mp4",
    "audio": "narration.mp3",
    "music": "music.mp3",
    "subtitles": "subtitles.srt",
    "overlay": "overlay.png",
}


def create_project(config: ProjectConfig, owner_id: int = None) -> dict:
    project_id = str(uuid.uuid4())[:8]
    project_dir = PROJECTS_DIR / project_id
    for subdir in ["video", "audio", "music", "subtitles", "overlay", "output"]:
        (project_dir / subdir).mkdir(parents=True, exist_ok=True)

    layers = {
        "video": LayerStatus.empty,
        "audio": LayerStatus.empty,
        "music": LayerStatus.empty,
        "subtitles": LayerStatus.empty,
        "overlay": LayerStatus.empty,
    }

    db = SessionLocal()
    try:
        project = Project(
            id=project_id,
            title=config.title,
            topic=config.topic,
            match=config.match or "",
            match_date=config.match_date or "",
            category=getattr(config, "category", "") or "",
            tags=json.dumps([], ensure_ascii=False),
            config=json.dumps(config.model_dump(), ensure_ascii=False),
            layers=json.dumps(layers, ensure_ascii=False),
            layer_info=json.dumps({}, ensure_ascii=False),
            output=None,
            owner_id=owner_id,
        )
        db.add(project)
        db.commit()
        db.refresh(project)
        return project.to_dict()
    finally:
        db.close()


def get_project(project_id: str) -> dict:
    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.id == project_id).first()
        return project.to_dict() if project else None
    finally:
        db.close()


def list_projects(owner_id: int = None, category: str = None, tag: str = None) -> list:
    db = SessionLocal()
    try:
        q = db.query(Project)
        if owner_id is not None:
            q = q.filter(Project.owner_id == owner_id)
        if category:
            q = q.filter(Project.category == category)
        if tag:
            q = q.filter(Project.tags.contains(f'"{tag}"'))
        projects = q.order_by(Project.created_at.desc()).all()
        return [p.to_dict() for p in projects]
    finally:
        db.close()


def update_layer_status(project_id: str, layer: str, status: LayerStatus, info: dict = None):
    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            return None
        layers = json.loads(project.layers or "{}")
        layers[layer] = status
        project.layers = json.dumps(layers, ensure_ascii=False)

        if info:
            layer_info = json.loads(project.layer_info or "{}")
            layer_info[layer] = info
            project.layer_info = json.dumps(layer_info, ensure_ascii=False)

        db.commit()
        db.refresh(project)
        return project.to_dict()
    finally:
        db.close()


def update_project_config(project_id: str, updates: dict) -> dict:
    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            return None
        config = json.loads(project.config or "{}")
        config.update(updates)
        project.config = json.dumps(config, ensure_ascii=False)
        db.commit()
        db.refresh(project)
        return project.to_dict()
    finally:
        db.close()


def update_tags(project_id: str, tags: list) -> dict:
    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            return None
        project.tags = json.dumps(tags, ensure_ascii=False)
        db.commit()
        db.refresh(project)
        return project.to_dict()
    finally:
        db.close()


def update_category(project_id: str, category: str) -> dict:
    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            return None
        project.category = category
        db.commit()
        db.refresh(project)
        return project.to_dict()
    finally:
        db.close()


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


def get_project_size(project_id: str) -> dict:
    project_dir = PROJECTS_DIR / project_id
    if not project_dir.exists():
        return None
    sizes = {}
    total = 0
    for subdir in ["video", "audio", "music", "subtitles", "overlay", "output"]:
        d = project_dir / subdir
        s = sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) if d.exists() else 0
        sizes[subdir] = s
        total += s
    sizes["total"] = total
    return sizes


def get_all_stats() -> dict:
    db = SessionLocal()
    try:
        count = db.query(Project).count()
    finally:
        db.close()
    total = 0
    if PROJECTS_DIR.exists():
        for d in PROJECTS_DIR.iterdir():
            for f in d.rglob("*"):
                if f.is_file():
                    total += f.stat().st_size
    return {"project_count": count, "total_bytes": total, "total_mb": round(total / 1024 / 1024, 1)}


def bulk_delete(project_ids: list) -> dict:
    freed = 0
    deleted = []
    db = SessionLocal()
    try:
        for pid in project_ids:
            d = PROJECTS_DIR / pid
            if d.exists():
                for f in d.rglob("*"):
                    if f.is_file():
                        freed += f.stat().st_size
                shutil.rmtree(d)
            project = db.query(Project).filter(Project.id == pid).first()
            if project:
                db.delete(project)
                deleted.append(pid)
        db.commit()
    finally:
        db.close()
    return {"deleted": deleted, "freed_bytes": freed, "freed_mb": round(freed / 1024 / 1024, 1)}


def clear_renders(project_id: str) -> dict:
    output_dir = PROJECTS_DIR / project_id / "output"
    if not output_dir.exists():
        return {"freed_bytes": 0, "freed_mb": 0}
    freed = sum(f.stat().st_size for f in output_dir.rglob("*") if f.is_file())
    shutil.rmtree(output_dir)
    output_dir.mkdir()
    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.id == project_id).first()
        if project:
            project.output = None
            db.commit()
    finally:
        db.close()
    return {"freed_bytes": freed, "freed_mb": round(freed / 1024 / 1024, 1)}


def duplicate_project(project_id: str) -> dict:
    meta = get_project(project_id)
    if not meta:
        return None
    new_id = str(uuid.uuid4())[:8]
    src = PROJECTS_DIR / project_id
    dst = PROJECTS_DIR / new_id
    shutil.copytree(src, dst)

    db = SessionLocal()
    try:
        old = db.query(Project).filter(Project.id == project_id).first()
        if not old:
            return None
        new_project = Project(
            id=new_id,
            title=old.title + " (copia)",
            topic=old.topic,
            match=old.match,
            match_date=old.match_date,
            category=old.category,
            tags=old.tags,
            config=old.config,
            layers=old.layers,
            layer_info=old.layer_info,
            output=None,
            owner_id=old.owner_id,
        )
        db.add(new_project)
        db.commit()
        db.refresh(new_project)
        return new_project.to_dict()
    finally:
        db.close()


def delete_project(project_id: str) -> bool:
    d = PROJECTS_DIR / project_id
    if d.exists():
        shutil.rmtree(d)
    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.id == project_id).first()
        if project:
            db.delete(project)
            db.commit()
            return True
        return False
    finally:
        db.close()
