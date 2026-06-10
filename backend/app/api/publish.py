from fastapi import APIRouter, HTTPException
from pathlib import Path
import os

router = APIRouter()

@router.post("/{project_id}/youtube")
async def publish_youtube(project_id: str, body: dict):
    output_path = Path("projects") / project_id / "output" / "final.mp4"
    if not output_path.exists():
        raise HTTPException(status_code=404, detail="Render not found. Run render first.")

    project_from_service = __import__(
        "app.services.project_service", fromlist=["get_project"]
    ).get_project(project_id)

    title = body.get("title") or project_from_service["title"]
    description = body.get("description", "")
    tags = body.get("tags", ["Mundial2026", "Fútbol", "Copa del Mundo"])
    privacy = body.get("privacy", "public")

    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        from google.oauth2.credentials import Credentials

        creds_data = {
            "token": os.getenv("YOUTUBE_TOKEN"),
            "refresh_token": os.getenv("YOUTUBE_REFRESH_TOKEN"),
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": os.getenv("YOUTUBE_CLIENT_ID"),
            "client_secret": os.getenv("YOUTUBE_CLIENT_SECRET"),
        }
        creds = Credentials(**creds_data)
        youtube = build("youtube", "v3", credentials=creds)

        request = youtube.videos().insert(
            part="snippet,status",
            body={
                "snippet": {
                    "title": title,
                    "description": description,
                    "tags": tags,
                    "categoryId": "17",
                },
                "status": {"privacyStatus": privacy},
            },
            media_body=MediaFileUpload(str(output_path), chunksize=-1, resumable=True),
        )
        response = request.execute()
        video_id = response["id"]
        return {
            "status": "published",
            "video_id": video_id,
            "url": f"https://youtube.com/watch?v={video_id}",
        }

    except ImportError:
        return {
            "status": "mock",
            "message": "google-api-python-client not installed. Install to enable real publishing.",
            "would_publish": str(output_path),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
