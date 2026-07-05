import os
import asyncio
import logging
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger(__name__)

MIN_WIDTH = 720


async def _search_pexels_photos(query: str, count: int, orientation: str) -> list:
    key = os.getenv("PEXELS_API_KEY")
    log.info(f"[photo] Pexels Photos key={'SET' if key else 'MISSING'} query={query!r}")
    if not key:
        return []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.pexels.com/v1/search",
                headers={"Authorization": key},
                params={"query": query, "per_page": count, "orientation": orientation},
            )
        if resp.status_code != 200:
            log.warning(f"Pexels photos HTTP {resp.status_code}")
            return []
        photos = []
        for p in resp.json().get("photos", []):
            src = p.get("src", {})
            url = src.get("original") or src.get("large2x") or src.get("large")
            if not url:
                continue
            photos.append({
                "url": url,
                "thumbnail": src.get("medium"),
                "title": p.get("alt", ""),
                "width": p.get("width", 0),
                "height": p.get("height", 0),
            })
        log.debug(f"Pexels photos: {len(photos)} for '{query}'")
        return photos
    except Exception as e:
        log.warning(f"Pexels photos error: {e}")
        return []


async def _search_pixabay_photos(query: str, count: int, orientation: str) -> list:
    key = os.getenv("PIXABAY_API_KEY")
    if not key:
        return []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://pixabay.com/api/",
                params={
                    "key": key,
                    "q": query,
                    "image_type": "photo",
                    "per_page": count,
                    "orientation": orientation,  # "vertical" or "horizontal"
                    "safesearch": "true",
                    "min_width": MIN_WIDTH,
                },
            )
        if resp.status_code != 200:
            log.warning(f"Pixabay photos HTTP {resp.status_code}")
            return []
        photos = []
        for hit in resp.json().get("hits", []):
            url = hit.get("largeImageURL") or hit.get("webformatURL")
            if not url:
                continue
            photos.append({
                "url": url,
                "thumbnail": hit.get("previewURL"),
                "title": hit.get("tags", ""),
                "width": hit.get("imageWidth", 0),
                "height": hit.get("imageHeight", 0),
            })
        log.debug(f"Pixabay photos: {len(photos)} for '{query}'")
        return photos
    except Exception as e:
        log.warning(f"Pixabay photos error: {e}")
        return []


async def _search_wikimedia_photos(query: str, count: int, orientation: str) -> list:
    """Wikimedia Commons: imágenes libres, FILTRADAS a dominio público / CC0
    (sin obligación de atribución, para no necesitar créditos)."""
    def _is_free(em: dict) -> bool:
        lic = (em.get("License", {}).get("value") or "").lower()
        short = (em.get("LicenseShortName", {}).get("value") or "").lower()
        return (lic == "cc0" or lic.startswith("pd") or "cc-pd" in lic
                or "public domain" in short or "cc0" in short)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://commons.wikimedia.org/w/api.php",
                params={
                    "action": "query", "format": "json",
                    "generator": "search", "gsrsearch": query,
                    "gsrnamespace": 6, "gsrlimit": min(count * 5, 50),
                    "prop": "imageinfo", "iiprop": "url|mime|extmetadata",
                    "iiurlwidth": 1280,
                },
                headers={"User-Agent": "LayerCut/1.0 (video generator)"},
            )
        if resp.status_code != 200:
            log.warning(f"Wikimedia HTTP {resp.status_code}")
            return []
        pages = resp.json().get("query", {}).get("pages", {})
        photos = []
        for p in pages.values():
            ii = (p.get("imageinfo") or [{}])[0]
            if ii.get("mime") not in ("image/jpeg", "image/png"):
                continue
            if not _is_free(ii.get("extmetadata", {})):
                continue
            url = ii.get("thumburl") or ii.get("url")
            if not url:
                continue
            photos.append({
                "url": url,
                "thumbnail": ii.get("thumburl"),
                "title": p.get("title", ""),
                "width": ii.get("thumbwidth") or ii.get("width", 0),
                "height": ii.get("thumbheight") or ii.get("height", 0),
            })
        log.info(f"Wikimedia photos (PD/CC0): {len(photos)} for '{query}'")
        return photos[:count]
    except Exception as e:
        log.warning(f"Wikimedia photos error: {e}")
        return []


async def search_photos(query: str, count: int = 10, orientation: str = "portrait",
                        providers=None) -> list:
    """Busca fotos en los proveedores dados (por defecto Pexels + Pixabay) y las intercala."""
    providers = tuple(providers) if providers else ("pexels", "pixabay")
    pixabay_orient = "vertical" if orientation == "portrait" else "horizontal"

    tasks, names = [], []
    if "pexels" in providers:
        tasks.append(_search_pexels_photos(query, count, orientation)); names.append("pexels")
    if "pixabay" in providers:
        tasks.append(_search_pixabay_photos(query, count, pixabay_orient)); names.append("pixabay")
    if "wikimedia" in providers:
        tasks.append(_search_wikimedia_photos(query, count, orientation)); names.append("wikimedia")

    results = await asyncio.gather(*tasks, return_exceptions=True)
    lists = [r if isinstance(r, list) else [] for r in results]

    # Intercalar para variar entre fuentes
    interleaved = []
    for i in range(max((len(l) for l in lists), default=0)):
        for l in lists:
            if i < len(l):
                interleaved.append(l[i])

    log.info("Photos found: " + ", ".join(f"{len(l)} {n}" for n, l in zip(names, lists)) + f" for '{query}'")
    return interleaved[:count]


async def download_photos(photos: list, dest_dir: Path) -> list[Path]:
    """Download photos in parallel; returns paths of successful downloads."""
    dest_dir.mkdir(parents=True, exist_ok=True)

    async def _one(i: int, photo: dict) -> Optional[Path]:
        url = photo.get("url")
        if not url:
            return None
        raw_name = url.split("?")[0].split("/")[-1]
        ext = "jpg"
        if "." in raw_name:
            candidate = raw_name.rsplit(".", 1)[-1].lower()
            if candidate in ("jpg", "jpeg", "png", "webp"):
                ext = candidate
        dest = dest_dir / f"photo_{i:02d}.{ext}"
        if dest.exists() and dest.stat().st_size > 2000:
            return dest
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
                resp = await client.get(url, headers={
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
                })
            if resp.status_code == 200 and len(resp.content) > 2000:
                dest.write_bytes(resp.content)
                log.debug(f"Downloaded photo {i}: {dest.name}")
                return dest
            log.warning(f"Photo {i} HTTP {resp.status_code}: {url[:80]}")
        except Exception as e:
            log.warning(f"Failed to download photo {i}: {e}")
        return None

    results = await asyncio.gather(*[_one(i, p) for i, p in enumerate(photos)],
                                   return_exceptions=True)
    paths = [r for r in results if isinstance(r, Path) and r is not None]
    log.info(f"Downloaded {len(paths)}/{len(photos)} photos to {dest_dir}")
    return paths


def _kenburns_vf(idx: int, frames: int, out_w: int, out_h: int) -> str:
    """FFmpeg filtergraph for Ken Burns effect alternating zoom-in / zoom-out."""
    speed = round(0.3 / max(frames, 1), 7)
    if idx % 2 == 0:
        z_expr = f"min(zoom+{speed},1.3)"
    else:
        z_expr = f"if(eq(on,1),1.3,max(zoom-{speed},1.0))"
    scale_w, scale_h = out_w * 2, out_h * 2
    return (
        f"scale={scale_w}:{scale_h}:force_original_aspect_ratio=increase,"
        f"crop={scale_w}:{scale_h},"
        f"zoompan=z='{z_expr}':d={frames}:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"s={out_w}x{out_h},"
        f"fps=25"
    )


async def photo_to_clip(
    photo: Path,
    output: Path,
    duration: float,
    aspect: str = "9:16",
    idx: int = 0,
) -> Optional[Path]:
    """Convert a still image to a video clip with Ken Burns zoom effect via FFmpeg."""
    out_w, out_h = (1080, 1920) if aspect == "9:16" else (1920, 1080)
    frames = max(int(duration * 25), 25)
    vf = _kenburns_vf(idx, frames, out_w, out_h)

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(photo),
        "-vf", vf,
        "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-crf", "23", "-preset", "fast",
        "-an",
        str(output),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        log.error(f"Ken Burns failed for {photo.name}: {stderr.decode()[-400:]}")
        return None
    return output


async def fetch_photo_clips(
    query: str,
    dest_dir: Path,
    count: int = 6,
    duration: float = 4.0,
    aspect: str = "9:16",
    on_progress=None,
    providers=None,
) -> list[Path]:
    """Search → download → Ken Burns conversion. providers: proveedores de fotos
    (por defecto Pexels + Pixabay; p.ej. ('wikimedia',) para Commons)."""
    log.info(f"[photo] fetch_photo_clips called: query={query!r} count={count} aspect={aspect} providers={providers}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    originals_dir = dest_dir / "originals"
    originals_dir.mkdir(exist_ok=True)

    orientation = "portrait" if aspect == "9:16" else "landscape"

    async def _emit(pct: int, msg: str = ""):
        if on_progress:
            await on_progress({"type": "progress", "task_type": "video", "progress": pct, "msg": msg})

    await _emit(5, "Buscando fotos...")
    photos = await search_photos(query, count=count + 4, orientation=orientation, providers=providers)
    if not photos:
        log.warning(f"No photos found for '{query}'")
        return []

    await _emit(15, f"Descargando {min(len(photos), count + 4)} fotos...")
    downloaded = await download_photos(photos[:count + 4], originals_dir)
    good = downloaded[:count]

    if not good:
        log.warning(f"All photo downloads failed for '{query}'")
        return []

    await _emit(30, f"Aplicando efecto Ken Burns a {len(good)} fotos...")
    clips: list[Path] = []
    for i, photo_path in enumerate(good):
        clip_out = dest_dir / f"clip_{i:03d}.mp4"
        result = await photo_to_clip(photo_path, clip_out, duration, aspect, idx=i)
        if result:
            clips.append(result)
        await _emit(30 + int(60 * (i + 1) / len(good)))

    log.info(f"Generated {len(clips)} photo clips for '{query}'")
    return clips
