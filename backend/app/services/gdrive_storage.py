import os
import json
import base64
import asyncio
import logging
import shutil
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive"]
DB_FOLDER_NAME = "db"
PROJECTS_FOLDER_NAME = "projects"
DB_FILENAME = "layercut.db"
LOCAL_DB_PATH = Path("./layercut.db")


def _credentials():
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        return None
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        try:
            info = json.loads(base64.b64decode(raw).decode("utf-8"))
        except Exception as e:
            log.error(f"GOOGLE_SERVICE_ACCOUNT_JSON inválido: {e}")
            return None
    from google.oauth2 import service_account
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)


def _client():
    creds = _credentials()
    if not creds:
        return None
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _find_folder(svc, name: str, parent_id: str = None) -> Optional[str]:
    q = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        q += f" and '{parent_id}' in parents"
    res = svc.files().list(q=q, fields="files(id,name)", pageSize=1).execute()
    files = res.get("files", [])
    return files[0]["id"] if files else None


def _create_folder(svc, name: str, parent_id: str = None) -> str:
    meta = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        meta["parents"] = [parent_id]
    f = svc.files().create(body=meta, fields="id").execute()
    return f["id"]


def _get_or_create_folder(svc, name: str, parent_id: str = None) -> str:
    fid = _find_folder(svc, name, parent_id)
    if fid:
        return fid
    return _create_folder(svc, name, parent_id)


def _find_file(svc, name: str, parent_id: str) -> Optional[str]:
    q = f"name='{name}' and '{parent_id}' in parents and trashed=false"
    res = svc.files().list(q=q, fields="files(id,name,modifiedTime)", pageSize=1).execute()
    files = res.get("files", [])
    return files[0]["id"] if files else None


def _upload_file(svc, local_path: Path, parent_id: str, filename: str = None) -> str:
    from googleapiclient.http import MediaFileUpload
    fname = filename or local_path.name
    existing_id = _find_file(svc, fname, parent_id)
    media = MediaFileUpload(str(local_path), resumable=True)

    if existing_id:
        f = svc.files().update(
            fileId=existing_id, media_body=media, fields="id"
        ).execute()
    else:
        meta = {"name": fname, "parents": [parent_id]}
        f = svc.files().create(body=meta, media_body=media, fields="id").execute()
    return f["id"]


def _download_file(svc, file_id: str, dest: Path):
    from googleapiclient.http import MediaIoBaseDownload
    req = svc.files().get_media(fileId=file_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, req, chunksize=5 * 1024 * 1024)
        done = False
        while not done:
            _, done = downloader.next_chunk()


def _get_root_folder_id() -> Optional[str]:
    return os.getenv("GDRIVE_STORAGE_FOLDER_ID")


def _get_storage_folders(svc) -> Optional[dict]:
    root_id = _get_root_folder_id()
    if not root_id:
        log.warning("GDRIVE_STORAGE_FOLDER_ID no configurado — persistencia en Drive deshabilitada")
        return None
    db_id = _get_or_create_folder(svc, DB_FOLDER_NAME, root_id)
    projects_id = _get_or_create_folder(svc, PROJECTS_FOLDER_NAME, root_id)
    return {"root": root_id, "db": db_id, "projects": projects_id}


async def restore_db():
    svc = _client()
    if not svc:
        log.warning("Google Drive no configurado — sin persistencia remota")
        return False

    try:
        folders = await asyncio.to_thread(_get_storage_folders, svc)
        if not folders:
            return False
        file_id = await asyncio.to_thread(_find_file, svc, DB_FILENAME, folders["db"])
        if not file_id:
            log.info("No hay DB en Drive — se usará la local")
            return False

        backup = LOCAL_DB_PATH.with_suffix(".db.bak")
        if LOCAL_DB_PATH.exists():
            shutil.copy2(LOCAL_DB_PATH, backup)

        await asyncio.to_thread(_download_file, svc, file_id, LOCAL_DB_PATH)
        log.info(f"DB restaurada desde Drive ({LOCAL_DB_PATH.stat().st_size} bytes)")
        return True
    except Exception as e:
        log.error(f"Error restaurando DB desde Drive: {e}")
        if LOCAL_DB_PATH.with_suffix(".db.bak").exists():
            shutil.copy2(LOCAL_DB_PATH.with_suffix(".db.bak"), LOCAL_DB_PATH)
        return False


async def backup_db():
    svc = _client()
    if not svc or not LOCAL_DB_PATH.exists():
        return False

    try:
        folders = await asyncio.to_thread(_get_storage_folders, svc)
        if not folders:
            return False
        await asyncio.to_thread(_upload_file, svc, LOCAL_DB_PATH, folders["db"], DB_FILENAME)
        log.info(f"DB respaldada en Drive ({LOCAL_DB_PATH.stat().st_size} bytes)")
        return True
    except Exception as e:
        log.error(f"Error respaldando DB en Drive: {e}")
        return False


async def upload_render(project_id: str, video_path: Path) -> Optional[str]:
    svc = _client()
    if not svc or not video_path.exists():
        return None

    try:
        folders = await asyncio.to_thread(_get_storage_folders, svc)
        if not folders:
            return None
        proj_folder = await asyncio.to_thread(
            _get_or_create_folder, svc, project_id, folders["projects"]
        )
        file_id = await asyncio.to_thread(
            _upload_file, svc, video_path, proj_folder, video_path.name
        )
        log.info(f"Render {video_path.name} subido a Drive (proyecto {project_id})")
        return file_id
    except Exception as e:
        log.error(f"Error subiendo render a Drive: {e}")
        return None


async def upload_project_files(project_id: str, project_dir: Path) -> int:
    svc = _client()
    if not svc or not project_dir.exists():
        return 0

    try:
        folders = await asyncio.to_thread(_get_storage_folders, svc)
        if not folders:
            return 0
        proj_folder = await asyncio.to_thread(
            _get_or_create_folder, svc, project_id, folders["projects"]
        )

        count = 0
        for subdir in ["video", "audio", "music", "subtitles", "output"]:
            sub_path = project_dir / subdir
            if not sub_path.exists():
                continue
            sub_drive = await asyncio.to_thread(
                _get_or_create_folder, svc, subdir, proj_folder
            )
            for f in sub_path.iterdir():
                if f.is_file() and f.stat().st_size > 0:
                    await asyncio.to_thread(_upload_file, svc, f, sub_drive, f.name)
                    count += 1

        log.info(f"Proyecto {project_id}: {count} archivos subidos a Drive")
        return count
    except Exception as e:
        log.error(f"Error subiendo proyecto {project_id} a Drive: {e}")
        return 0


async def restore_project_files(project_id: str, project_dir: Path) -> int:
    svc = _client()
    if not svc:
        return 0

    try:
        folders = await asyncio.to_thread(_get_storage_folders, svc)
        if not folders:
            return 0
        proj_id = await asyncio.to_thread(
            _find_folder, svc, project_id, folders["projects"]
        )
        if not proj_id:
            return 0

        count = 0
        for subdir in ["video", "audio", "music", "subtitles", "output"]:
            sub_id = await asyncio.to_thread(_find_folder, svc, subdir, proj_id)
            if not sub_id:
                continue
            q = f"'{sub_id}' in parents and trashed=false"
            res = await asyncio.to_thread(
                lambda: svc.files().list(q=q, fields="files(id,name)", pageSize=50).execute()
            )
            for f in res.get("files", []):
                dest = project_dir / subdir / f["name"]
                dest.parent.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(_download_file, svc, f["id"], dest)
                count += 1

        log.info(f"Proyecto {project_id}: {count} archivos restaurados desde Drive")
        return count
    except Exception as e:
        log.error(f"Error restaurando proyecto {project_id} desde Drive: {e}")
        return 0


async def list_stored_projects() -> list:
    svc = _client()
    if not svc:
        return []

    try:
        folders = await asyncio.to_thread(_get_storage_folders, svc)
        if not folders:
            return []
        q = f"'{folders['projects']}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        res = await asyncio.to_thread(
            lambda: svc.files().list(q=q, fields="files(id,name)", pageSize=200).execute()
        )
        return [{"id": f["name"], "drive_folder_id": f["id"]} for f in res.get("files", [])]
    except Exception as e:
        log.error(f"Error listando proyectos en Drive: {e}")
        return []
