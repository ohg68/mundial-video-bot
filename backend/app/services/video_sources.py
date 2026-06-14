import os
import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger(__name__)


async def search_pixabay(query: str, count: int = 12, orientation: str = "vertical") -> list:
    key = os.getenv("PIXABAY_API_KEY")
    if not key:
        return []
    orient = "vertical" if orientation == "portrait" else "horizontal"
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://pixabay.com/api/videos/",
            params={
                "key": key,
                "q": query,
                "per_page": count,
                "video_type": "film",
                "orientation": orient,
            },
            timeout=15,
        )
    data = resp.json()
    results = []
    for hit in data.get("hits", []):
        videos = hit.get("videos", {})
        large = videos.get("large", {})
        medium = videos.get("medium", {})
        small = videos.get("small", {})
        best = large or medium or small
        if best:
            results.append({
                "id": f"pixabay_{hit['id']}",
                "source": "pixabay",
                "title": hit.get("tags", ""),
                "thumbnail": f"https://i.vimeocdn.com/video/{hit['picture_id']}_295x166.jpg"
                    if hit.get("picture_id") else None,
                "url": best.get("url"),
                "duration": hit.get("duration", 0),
                "width": best.get("width", 0),
                "height": best.get("height", 0),
            })
    return results


async def search_pexels(query: str, count: int = 12, orientation: str = "portrait") -> list:
    key = os.getenv("PEXELS_API_KEY")
    if not key:
        return []
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": key},
            params={"query": query, "per_page": count, "orientation": orientation},
            timeout=15,
        )
    data = resp.json()
    results = []
    for v in data.get("videos", []):
        thumb = v.get("image")
        best_file = None
        for f in v.get("video_files", []):
            if f.get("quality") == "hd":
                best_file = f
                break
        if not best_file and v.get("video_files"):
            best_file = v["video_files"][0]
        if best_file:
            results.append({
                "id": f"pexels_{v['id']}",
                "source": "pexels",
                "title": v.get("url", "").split("/")[-2].replace("-", " ") if v.get("url") else "",
                "thumbnail": thumb,
                "url": best_file["link"],
                "duration": v.get("duration", 0),
                "width": best_file.get("width", 0),
                "height": best_file.get("height", 0),
            })
    return results


async def search_coverr(query: str, count: int = 12) -> list:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.coverr.co/videos",
            params={"query": query, "page_size": count},
            timeout=15,
        )
    if resp.status_code != 200:
        return []
    data = resp.json()
    results = []
    for v in data.get("hits", data.get("videos", [])):
        urls = v.get("urls", {})
        results.append({
            "id": f"coverr_{v.get('id', '')}",
            "source": "coverr",
            "title": v.get("title", ""),
            "thumbnail": v.get("thumbnail", urls.get("poster")),
            "url": urls.get("mp4_download") or urls.get("mp4_preview"),
            "duration": v.get("duration", 0),
            "width": 1920,
            "height": 1080,
        })
    return results


async def search_youtube(query: str, count: int = 12, creative_commons: bool = True) -> list:
    try:
        cmd = [
            "yt-dlp", "--flat-playlist", "--no-download",
            "--print", "%(id)s|%(title)s|%(duration)s|%(thumbnail)s",
            f"ytsearch{count}:{query}",
        ]
        if creative_commons:
            cmd.insert(1, "--match-filter")
            cmd.insert(2, "license=creativeCommon")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
    except FileNotFoundError:
        log.warning("yt-dlp not installed")
        return []

    results = []
    for line in stdout.decode().strip().split("\n"):
        if not line or "|" not in line:
            continue
        parts = line.split("|", 3)
        vid_id = parts[0]
        title = parts[1] if len(parts) > 1 else ""
        duration = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        thumb = parts[3] if len(parts) > 3 else f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"
        results.append({
            "id": f"youtube_{vid_id}",
            "source": "youtube",
            "title": title,
            "thumbnail": thumb,
            "url": f"https://www.youtube.com/watch?v={vid_id}",
            "duration": duration,
            "width": 1920,
            "height": 1080,
        })
    return results


async def search_clips(query: str, sources: list[str] = None, count: int = 12) -> list:
    if sources is None:
        sources = ["pexels", "pixabay"]

    tasks = []
    for src in sources:
        if src == "pexels":
            tasks.append(search_pexels(query, count))
        elif src == "pixabay":
            tasks.append(search_pixabay(query, count))
        elif src == "coverr":
            tasks.append(search_coverr(query, count))
        elif src == "youtube":
            tasks.append(search_youtube(query, count))

    all_results = await asyncio.gather(*tasks, return_exceptions=True)
    clips = []
    for result in all_results:
        if isinstance(result, list):
            clips.extend(result)
    return clips


async def download_clip(clip: dict, dest_dir: Path) -> Optional[Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{clip['id']}.mp4"
    dest = dest_dir / filename

    if dest.exists():
        return dest

    if clip["source"] == "youtube":
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp", "-f", "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
            "--merge-output-format", "mp4",
            "-o", str(dest),
            clip["url"],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        return dest if dest.exists() else None

    async with httpx.AsyncClient() as client:
        resp = await client.get(clip["url"], timeout=60, follow_redirects=True)
        if resp.status_code == 200:
            dest.write_bytes(resp.content)
            return dest
    return None
