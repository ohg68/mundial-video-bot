"""Migrate existing JSON project files to SQLite database."""
import json
import logging
from pathlib import Path
from datetime import datetime
from app.database import SessionLocal, Project, init_db

log = logging.getLogger(__name__)
PROJECTS_DIR = Path("projects")


def migrate_json_to_db():
    init_db()
    if not PROJECTS_DIR.exists():
        log.info("No projects directory found, skipping migration")
        return 0

    db = SessionLocal()
    migrated = 0
    try:
        for project_dir in PROJECTS_DIR.iterdir():
            meta_path = project_dir / "project.json"
            if not meta_path.exists():
                continue

            meta = json.loads(meta_path.read_text())
            project_id = meta.get("id", project_dir.name)

            existing = db.query(Project).filter(Project.id == project_id).first()
            if existing:
                continue

            config = meta.get("config", {})
            layers = meta.get("layers", {})
            layer_info = meta.get("layer_info", {})

            created_at = None
            if meta.get("created_at"):
                try:
                    created_at = datetime.fromisoformat(meta["created_at"])
                except (ValueError, TypeError):
                    created_at = datetime.utcnow()

            project = Project(
                id=project_id,
                title=meta.get("title", ""),
                topic=meta.get("topic", config.get("topic", "")),
                match=meta.get("match", config.get("match", "")),
                match_date=meta.get("match_date", config.get("match_date", "")),
                category=config.get("category", ""),
                tags=json.dumps(config.get("tags", []), ensure_ascii=False),
                config=json.dumps(config, ensure_ascii=False),
                layers=json.dumps(layers, ensure_ascii=False),
                layer_info=json.dumps(layer_info, ensure_ascii=False),
                output=meta.get("output"),
                created_at=created_at or datetime.utcnow(),
            )
            db.add(project)
            migrated += 1
            log.info(f"Migrated project: {project_id} — {meta.get('title', '')}")

        db.commit()
        log.info(f"Migration complete: {migrated} projects migrated")
    except Exception as e:
        db.rollback()
        log.error(f"Migration failed: {e}")
        raise
    finally:
        db.close()

    return migrated


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    count = migrate_json_to_db()
    print(f"Migrated {count} projects")
