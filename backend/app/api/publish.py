import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db, ScheduledPost
from app.services import publish_service

router = APIRouter()


@router.post("/{project_id}/youtube")
async def publish_youtube(project_id: str, body: dict):
    result = await publish_service.publish_youtube(project_id, title=body.get("title", ""), **body)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("message"))
    return result


@router.post("/{project_id}/tiktok")
async def publish_tiktok(project_id: str, body: dict):
    result = await publish_service.publish_tiktok(project_id, title=body.get("title", ""), **body)
    return result


@router.post("/{project_id}/instagram")
async def publish_instagram(project_id: str, body: dict):
    result = await publish_service.publish_instagram(
        project_id, caption=body.get("caption", body.get("title", "")), **body
    )
    return result


@router.post("/{project_id}/multi")
async def publish_multi(project_id: str, body: dict):
    platforms = body.get("platforms", [])
    if not platforms:
        raise HTTPException(status_code=400, detail="No platforms specified")
    meta = {k: v for k, v in body.items() if k != "platforms"}
    results = await publish_service.publish_multi(project_id, platforms, meta)
    return {"results": results}


@router.post("/{project_id}/thumbnail")
async def generate_thumbnail(project_id: str, body: dict = None):
    body = body or {}
    timestamp = body.get("timestamp", 2.0)
    path = await publish_service.generate_thumbnail(project_id, timestamp)
    return {"path": str(path), "size_bytes": path.stat().st_size}


@router.post("/{project_id}/schedule")
async def schedule_post(
    project_id: str,
    body: dict,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    platform = body.get("platform")
    scheduled_at = body.get("scheduled_at")
    if not platform or not scheduled_at:
        raise HTTPException(status_code=400, detail="platform and scheduled_at required")

    post = ScheduledPost(
        project_id=project_id,
        platform=platform,
        scheduled_at=datetime.fromisoformat(scheduled_at),
        meta=json.dumps({k: v for k, v in body.items() if k not in ("platform", "scheduled_at")}),
    )
    db.add(post)
    db.commit()
    return {
        "id": post.id,
        "platform": post.platform,
        "scheduled_at": post.scheduled_at.isoformat(),
        "status": post.status,
    }


@router.get("/{project_id}/schedule")
async def list_scheduled(
    project_id: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    posts = db.query(ScheduledPost).filter(
        ScheduledPost.project_id == project_id
    ).order_by(ScheduledPost.scheduled_at).all()
    return {
        "posts": [
            {
                "id": p.id,
                "platform": p.platform,
                "scheduled_at": p.scheduled_at.isoformat(),
                "status": p.status,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in posts
        ]
    }


@router.delete("/{project_id}/schedule/{post_id}")
async def cancel_scheduled(
    project_id: str,
    post_id: int,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    post = db.query(ScheduledPost).filter(
        ScheduledPost.id == post_id, ScheduledPost.project_id == project_id
    ).first()
    if not post:
        raise HTTPException(status_code=404, detail="Scheduled post not found")
    if post.status != "pending":
        raise HTTPException(status_code=400, detail="Can only cancel pending posts")
    db.delete(post)
    db.commit()
    return {"ok": True}
