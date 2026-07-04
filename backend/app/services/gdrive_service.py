"""
Google Drive como fuente de video (opción: carpeta curada + cuenta de servicio).

Setup:
- `GOOGLE_SERVICE_ACCOUNT_JSON`: la clave JSON de la cuenta de servicio (cruda o base64).
- Compartir la carpeta de videos de Drive con el email de esa cuenta de servicio.
- (opcional) `GDRIVE_VIDEO_FOLDER_ID`: carpeta por defecto si el proyecto no trae una.

No usa ningún LLM: solo lista archivos, descarga y recorta con FFmpeg.
"""
import os
import json
import base64
import asyncio
import logging
from pathlib import Path

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


def _credentials():
    """Credenciales de la cuenta de servicio desde el entorno (JSON crudo o base64)."""
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        return None
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        try:
            info = json.loads(base64.b64decode(raw).decode("utf-8"))
        except Exception as e:
            log.error(f"GOOGLE_SERVICE_ACCOUNT_JSON inválido (ni JSON ni base64): {e}")
            return None
    from google.oauth2 import service_account
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)


def _client():
    creds = _credentials()
    if not creds:
        return None
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def is_configured() -> bool:
    return bool(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"))


def service_account_email() -> str:
    """Email de la cuenta de servicio (para saber con quién compartir la carpeta)."""
    creds = _credentials()
    return getattr(creds, "service_account_email", "") if creds else ""


def list_folders() -> list:
    """Carpetas visibles para la cuenta de servicio (las que le compartiste)."""
    svc = _client()
    if not svc:
        return []
    try:
        res = svc.files().list(
            q="mimeType='application/vnd.google-apps.folder' and trashed=false",
            fields="files(id,name)", pageSize=100, orderBy="name",
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        return res.get("files", [])
    except Exception as e:
        log.error(f"list_folders falló: {e}")
        return []


def list_videos(folder_id: str) -> list:
    """Archivos de video dentro de una carpeta (mimeType video/*)."""
    svc = _client()
    if not svc:
        return []
    try:
        res = svc.files().list(
            q=f"'{folder_id}' in parents and trashed=false and mimeType contains 'video/'",
            fields="files(id,name,size,mimeType)", pageSize=200,
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        return res.get("files", [])
    except Exception as e:
        log.error(f"list_videos({folder_id}) falló: {e}")
        return []


def _download_file(svc, file_id: str, dest: Path):
    from googleapiclient.http import MediaIoBaseDownload
    req = svc.files().get_media(fileId=file_id, supportsAllDrives=True)
    with open(dest, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, req, chunksize=5 * 1024 * 1024)
        done = False
        while not done:
            _, done = downloader.next_chunk()


async def _trim_scale(src: Path, out: Path, duration: float, aspect: str) -> bool:
    """Recorta los primeros `duration` s y normaliza a la resolución del aspecto (sin audio)."""
    w, h = (1080, 1920) if aspect == "9:16" else (1920, 1080)
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-i", str(src), "-t", f"{duration:.2f}",
        "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}",
        "-c:v", "libx264", "-crf", "23", "-preset", "fast", "-an",
        str(out),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        log.warning(f"trim/scale de {src.name} falló: {stderr.decode()[-300:]}")
        return False
    return out.exists()


async def fetch_gdrive_clips(folder_id: str, dest_dir: Path, count: int = 6,
                             duration: float = 4.0, aspect: str = "9:16") -> list:
    """Descarga hasta `count` videos de la carpeta de Drive, recorta/normaliza cada uno
    y devuelve las rutas de los clips listos. Vacío si no hay config/carpeta/videos."""
    svc = _client()
    if not svc:
        log.warning("GOOGLE_SERVICE_ACCOUNT_JSON no configurado — Drive deshabilitado")
        return []

    videos = await asyncio.to_thread(list_videos, folder_id)
    if not videos:
        log.warning(f"Carpeta de Drive {folder_id} sin videos (o no compartida con la cuenta de servicio)")
        return []

    # Barajar para variar entre renders; tomar hasta `count`
    import random
    random.shuffle(videos)
    picked = videos[:count]

    dest_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = dest_dir / "raw"
    raw_dir.mkdir(exist_ok=True)

    clips = []
    for i, v in enumerate(picked):
        raw = raw_dir / f"src_{i}.mp4"
        try:
            await asyncio.to_thread(_download_file, svc, v["id"], raw)
        except Exception as e:
            log.warning(f"Descarga de Drive '{v.get('name')}' falló: {e}")
            continue
        out = dest_dir / f"clip_{i:03d}.mp4"
        if await _trim_scale(raw, out, duration, aspect):
            clips.append(out)
        raw.unlink(missing_ok=True)  # liberar espacio (Railway es efímero)

    log.info(f"Drive: {len(clips)} clips listos de la carpeta {folder_id}")
    return clips
