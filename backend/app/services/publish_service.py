import asyncio
import json
import logging
import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from app.services import project_service

log = logging.getLogger(__name__)


async def generate_thumbnail(project_id: str, timestamp: float = 2.0) -> Path:
    project_dir = Path("projects") / project_id
    video_path = project_service.get_layer_path(project_id, "video")
    if not video_path.exists():
        output_path = project_dir / "output" / "final.mp4"
        if output_path.exists():
            video_path = output_path
        else:
            raise ValueError("No video source for thumbnail")

    thumb_path = project_dir / "output" / "thumbnail.jpg"
    thumb_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(timestamp),
        "-i", str(video_path),
        "-vframes", "1",
        "-q:v", "2",
        str(thumb_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"Thumbnail error: {stderr.decode()}")
    return thumb_path


async def publish_tiktok(project_id: str, title: str, **kwargs) -> dict:
    output_path = _get_output(project_id)
    access_token = os.getenv("TIKTOK_ACCESS_TOKEN")
    if not access_token:
        return {"status": "mock", "platform": "tiktok",
                "message": "TIKTOK_ACCESS_TOKEN not configured"}

    try:
        import httpx
        async with httpx.AsyncClient() as client:
            init = await client.post(
                "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/",
                headers={"Authorization": f"Bearer {access_token}",
                         "Content-Type": "application/json"},
                json={
                    "post_info": {"title": title[:150], "privacy_level": "PUBLIC_TO_EVERYONE"},
                    "source_info": {"source": "FILE_UPLOAD", "video_size": output_path.stat().st_size},
                },
            )
            data = init.json()
            if "error" in data and data["error"].get("code") != "ok":
                return {"status": "error", "platform": "tiktok", "detail": data}

            upload_url = data.get("data", {}).get("upload_url")
            if upload_url:
                with open(output_path, "rb") as f:
                    await client.put(upload_url, content=f.read(),
                                     headers={"Content-Type": "video/mp4"})

            return {"status": "published", "platform": "tiktok",
                    "publish_id": data.get("data", {}).get("publish_id")}
    except ImportError:
        return {"status": "mock", "platform": "tiktok",
                "message": "httpx not installed"}


async def publish_instagram(project_id: str, caption: str, **kwargs) -> dict:
    output_path = _get_output(project_id)
    access_token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
    ig_user_id = os.getenv("INSTAGRAM_USER_ID")
    if not access_token or not ig_user_id:
        return {"status": "mock", "platform": "instagram",
                "message": "INSTAGRAM_ACCESS_TOKEN/INSTAGRAM_USER_ID not configured"}

    try:
        import httpx
        async with httpx.AsyncClient() as client:
            create = await client.post(
                f"https://graph.facebook.com/v19.0/{ig_user_id}/media",
                data={
                    "media_type": "REELS",
                    "video_url": kwargs.get("video_url", ""),
                    "caption": caption[:2200],
                    "access_token": access_token,
                },
            )
            container_id = create.json().get("id")
            if not container_id:
                return {"status": "error", "platform": "instagram", "detail": create.json()}

            await asyncio.sleep(10)

            pub = await client.post(
                f"https://graph.facebook.com/v19.0/{ig_user_id}/media_publish",
                data={"creation_id": container_id, "access_token": access_token},
            )
            return {"status": "published", "platform": "instagram",
                    "media_id": pub.json().get("id")}
    except ImportError:
        return {"status": "mock", "platform": "instagram",
                "message": "httpx not installed"}


async def publish_youtube(project_id: str, title: str, **kwargs) -> dict:
    output_path = _get_output(project_id)
    description = kwargs.get("description", "")
    tags = kwargs.get("tags", ["Mundial2026", "Fútbol"])
    privacy = kwargs.get("privacy", "public")

    if not os.getenv("YOUTUBE_TOKEN"):
        return {"status": "error", "platform": "youtube",
                "message": "YOUTUBE_TOKEN no configurado en Railway"}

    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        from google.oauth2.credentials import Credentials

        creds = Credentials(
            token=os.getenv("YOUTUBE_TOKEN"),
            refresh_token=os.getenv("YOUTUBE_REFRESH_TOKEN"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.getenv("YOUTUBE_CLIENT_ID"),
            client_secret=os.getenv("YOUTUBE_CLIENT_SECRET"),
        )
        youtube = build("youtube", "v3", credentials=creds)
        req = youtube.videos().insert(
            part="snippet,status",
            body={
                "snippet": {"title": title, "description": description,
                            "tags": tags, "categoryId": "17"},
                "status": {"privacyStatus": privacy},
            },
            media_body=MediaFileUpload(str(output_path), chunksize=-1, resumable=True),
        )
        resp = req.execute()
        vid = resp["id"]
        return {"status": "published", "platform": "youtube",
                "video_id": vid, "url": f"https://youtube.com/watch?v={vid}"}
    except ImportError:
        return {"status": "mock", "platform": "youtube",
                "message": "google-api-python-client not installed"}
    except Exception as e:
        return {"status": "error", "platform": "youtube", "message": str(e)}


PUBLISHERS = {
    "youtube": publish_youtube,
    "tiktok": publish_tiktok,
    "instagram": publish_instagram,
}


async def publish_multi(project_id: str, platforms: list[str], meta: dict) -> list[dict]:
    results = []
    for p in platforms:
        fn = PUBLISHERS.get(p)
        if not fn:
            results.append({"status": "error", "platform": p, "message": "Unknown platform"})
            continue
        try:
            r = await fn(project_id, **meta)
            results.append(r)
        except Exception as e:
            results.append({"status": "error", "platform": p, "message": str(e)})
    return results


def _get_output(project_id: str) -> Path:
    p = Path("projects") / project_id / "output" / "final.mp4"
    if not p.exists():
        raise ValueError("Render not found — run render first")
    return p
