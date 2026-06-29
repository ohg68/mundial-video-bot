import secrets
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db, ShareLink
from app.services import project_service

router = APIRouter()


@router.post("/{project_id}/create")
async def create_share_link(
    project_id: str,
    body: dict = None,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    body = body or {}
    meta = project_service.get_project(project_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Project not found")

    hours = body.get("expires_hours", 72)
    expires_at = datetime.utcnow() + timedelta(hours=hours) if hours else None

    link = ShareLink(
        id=secrets.token_hex(8),
        project_id=project_id,
        token=secrets.token_urlsafe(32),
        expires_at=expires_at,
    )
    db.add(link)
    db.commit()

    return {
        "share_url": f"/share/{link.token}",
        "token": link.token,
        "expires_at": expires_at.isoformat() if expires_at else None,
    }


@router.get("/{project_id}/links")
async def list_share_links(
    project_id: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    links = db.query(ShareLink).filter(ShareLink.project_id == project_id).all()
    return {
        "links": [
            {
                "id": l.id,
                "token": l.token,
                "share_url": f"/share/{l.token}",
                "expires_at": l.expires_at.isoformat() if l.expires_at else None,
                "views": l.views,
                "created_at": l.created_at.isoformat() if l.created_at else None,
            }
            for l in links
        ]
    }


@router.delete("/{project_id}/links/{link_id}")
async def delete_share_link(
    project_id: str,
    link_id: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    link = db.query(ShareLink).filter(
        ShareLink.id == link_id, ShareLink.project_id == project_id
    ).first()
    if not link:
        raise HTTPException(status_code=404, detail="Link not found")
    db.delete(link)
    db.commit()
    return {"ok": True}


@router.get("/view/{token}")
async def view_share(token: str, db: Session = Depends(get_db)):
    link = db.query(ShareLink).filter(ShareLink.token == token).first()
    if not link:
        raise HTTPException(status_code=404, detail="Link not found or expired")
    if link.expires_at and link.expires_at < datetime.utcnow():
        raise HTTPException(status_code=410, detail="Link expired")

    link.views = (link.views or 0) + 1
    db.commit()

    meta = project_service.get_project(link.project_id)
    title = meta["title"] if meta else "Video"
    match = meta.get("match", "") if meta else ""

    share_html = _build_share_page(title, match, link.project_id, token)
    return HTMLResponse(content=share_html)


@router.get("/video/{token}")
async def stream_share_video(token: str, db: Session = Depends(get_db)):
    link = db.query(ShareLink).filter(ShareLink.token == token).first()
    if not link:
        raise HTTPException(status_code=404, detail="Link not found")
    if link.expires_at and link.expires_at < datetime.utcnow():
        raise HTTPException(status_code=410, detail="Link expired")

    output = Path("projects") / link.project_id / "output" / "final.mp4"
    if not output.exists():
        raise HTTPException(status_code=404, detail="Video not rendered yet")
    return FileResponse(output, media_type="video/mp4")


@router.get("/thumb/{token}")
async def share_thumbnail(token: str, db: Session = Depends(get_db)):
    link = db.query(ShareLink).filter(ShareLink.token == token).first()
    if not link:
        raise HTTPException(status_code=404)
    thumb = Path("projects") / link.project_id / "output" / "thumbnail.jpg"
    if not thumb.exists():
        raise HTTPException(status_code=404)
    return FileResponse(thumb, media_type="image/jpeg")


def _build_share_page(title: str, match: str, project_id: str, token: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} — LayerCut</title>
<meta property="og:title" content="{title}">
<meta property="og:type" content="video.other">
<meta property="og:video" content="/api/share/video/{token}">
<meta property="og:image" content="/api/share/thumb/{token}">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,system-ui,sans-serif;background:#0a0a0a;color:#fff;
min-height:100dvh;display:flex;flex-direction:column;align-items:center;justify-content:center}}
.card{{max-width:480px;width:92%;background:#161616;border-radius:16px;overflow:hidden;
border:1px solid #222}}
video{{width:100%;display:block;background:#000}}
.info{{padding:16px 20px}}
.title{{font-size:16px;font-weight:600;margin-bottom:4px}}
.match{{font-size:13px;color:#888}}
.brand{{text-align:center;padding:12px;font-size:11px;color:#555}}
.brand b{{color:#3b82f6}}
</style>
</head>
<body>
<div class="card">
  <video src="/api/share/video/{token}" controls playsinline preload="metadata"
    poster="/api/share/thumb/{token}"></video>
  <div class="info">
    <div class="title">{title}</div>
    {"<div class='match'>⚽ " + match + "</div>" if match else ""}
  </div>
</div>
<div class="brand">Creado con <b>LayerCut</b> · Mundial 2026</div>
</body>
</html>"""
