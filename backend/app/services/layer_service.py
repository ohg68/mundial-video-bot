import os
import httpx
import asyncio
import base64
import subprocess
from pathlib import Path
from app.models.project import ProjectConfig, VideoSource, LayerStatus
from app.services import project_service

LOCAL_CLIPS_DIR = Path(os.getenv("LOCAL_CLIPS_DIR", "clips"))

# Voz por defecto de ElevenLabs. Se puede sobreescribir con la variable de
# entorno ELEVENLABS_VOICE_ID o desde la config del proyecto.
# "Sarah" multilingue funciona bien en espanol; cambiala por la que prefieras
# desde tu panel de ElevenLabs (Voice Lab -> copiar Voice ID).
DEFAULT_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2")


async def generate_script(project_id: str, config: ProjectConfig) -> str:
    project_service.update_layer_status(project_id, "audio", LayerStatus.pending)
    prompt = f"""Eres un locutor deportivo para un canal de YouTube del Mundial 2026.
Genera un guion en espanol para un video corto (90 segundos) sobre:
Tema: {config.topic}
Partido: {config.match or "Mundial 2026"}
Fecha: {config.match_date or ""}

El guion debe:
- Tener un gancho en los primeros 5 segundos
- Ser informativo y emocionante
- Terminar con una llamada a la accion (suscribete, comenta)
- Durar aproximadamente 90 segundos al leerlo
- Solo el texto que leera el locutor, sin indicaciones de escena

Responde SOLO con el guion, sin introduccion ni explicacion."""

    deepseek_key = os.getenv("DEEPSEEK_API_KEY")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.deepseek.com/chat/completions",
            headers={
                "Authorization": f"Bearer {deepseek_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek-chat",
                "max_tokens": 1000,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
    data = resp.json()
    if resp.status_code != 200 or "choices" not in data:
        raise RuntimeError(f"DeepSeek error (status {resp.status_code}): {data}")
    script = data["choices"][0]["message"]["content"]

    meta = project_service.get_project(project_id)
    meta["config"]["script"] = script
    project_dir = Path("projects") / project_id
    (project_dir / "project.json").write_text(
        __import__("json").dumps(meta, indent=2, ensure_ascii=False)
    )
    return script


def _fmt_ts(t: float) -> str:
    """Segundos -> formato SRT 'HH:MM:SS,mmm'."""
    if t < 0:
        t = 0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    if ms == 1000:
        ms = 0
        s += 1
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _build_srt_from_chars(chars, starts, ends, words_per_cue: int = 7) -> str:
    """Convierte los tiempos por caracter de ElevenLabs en un .srt agrupado.

    words_per_cue=7 da lineas de texto tipo karaoke (legibles), en vez de
    una o dos palabras gigantes por pantalla.
    """
    words = []
    cur_word = ""
    cur_start = None
    cur_end = None
    for ch, st, en in zip(chars, starts, ends):
        if ch in (" ", "\n", "\t"):
            if cur_word:
                words.append((cur_word, cur_start, cur_end))
                cur_word = ""
                cur_start = None
        else:
            if cur_start is None:
                cur_start = st
            cur_word += ch
            cur_end = en
    if cur_word:
        words.append((cur_word, cur_start, cur_end))

    lines = []
    idx = 1
    for i in range(0, len(words), words_per_cue):
        group = words[i:i + words_per_cue]
        if not group:
            continue
        start = group[0][1] if group[0][1] is not None else 0.0
        end = group[-1][2] if group[-1][2] is not None else start + 1.0
        text = " ".join(w[0] for w in group)
        lines.append(f"{idx}\n{_fmt_ts(start)} --> {_fmt_ts(end)}\n{text}\n")
        idx += 1
    return "\n".join(lines)


async def generate_audio(project_id: str, config: ProjectConfig) -> Path:
    """Genera la narracion con ElevenLabs y los subtitulos sincronizados."""
    project_service.update_layer_status(project_id, "audio", LayerStatus.pending)
    script = config.script or await generate_script(project_id, config)
    output_path = project_service.get_layer_path(project_id, "audio")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if config.audio.custom_file:
        import shutil
        shutil.copy2(config.audio.custom_file, output_path)
        project_service.update_layer_status(project_id, "audio", LayerStatus.ready, {
            "voice": "custom",
            "file": str(output_path),
        })
        return output_path

    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        raise RuntimeError("Falta la variable de entorno ELEVENLABS_API_KEY")

    voice_id = getattr(config.audio, "voice_id", None) or DEFAULT_VOICE_ID

    def _synthesize():
        from elevenlabs.client import ElevenLabs
        client = ElevenLabs(api_key=api_key)
        return client.text_to_speech.convert_with_timestamps(
            voice_id=voice_id,
            text=script,
            model_id=ELEVENLABS_MODEL,
            output_format="mp3_44100_128",
        )

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, _synthesize)
    except Exception as e:
        raise RuntimeError(f"ElevenLabs error: {e}")

    def _get(obj, *names, default=None):
        for n in names:
            if isinstance(obj, dict) and n in obj:
                return obj[n]
            if hasattr(obj, n):
                return getattr(obj, n)
        return default

    audio_b64 = _get(result, "audio_base_64", "audio_base64")
    alignment = _get(result, "alignment")

    if not audio_b64:
        raise RuntimeError("ElevenLabs no devolvio audio")

    tmp_mp3 = output_path.with_suffix(".raw.mp3")
    tmp_mp3.write_bytes(base64.b64decode(audio_b64))

    conv = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-i", str(tmp_mp3),
        "-acodec", "libmp3lame", "-q:a", "2",
        str(output_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await conv.communicate()
    tmp_mp3.unlink(missing_ok=True)

    if alignment is not None:
        chars = _get(alignment, "characters", default=[])
        starts = _get(alignment, "character_start_times_seconds",
                      "character_start_times", default=[])
        ends = _get(alignment, "character_end_times_seconds",
                    "character_end_times", default=[])
        if chars and starts and ends:
            srt = _build_srt_from_chars(list(chars), list(starts), list(ends))
            sub_path = project_service.get_layer_path(project_id, "subtitles")
            sub_path.parent.mkdir(parents=True, exist_ok=True)
            if sub_path.suffix.lower() != ".srt":
                sub_path = sub_path.with_suffix(".srt")
            sub_path.write_text(srt, encoding="utf-8")
            project_service.update_layer_status(project_id, "subtitles", LayerStatus.ready, {
                "file": str(sub_path),
                "synced_with": "audio",
            })

    project_service.update_layer_status(project_id, "audio", LayerStatus.ready, {
        "voice": voice_id,
        "file": str(output_path),
    })
    return output_path


async def generate_subtitles(project_id: str, config: ProjectConfig = None) -> Path:
    """Devuelve los subtitulos generados junto al audio."""
    sub_path = project_service.get_layer_path(project_id, "subtitles")
    candidates = [sub_path, sub_path.with_suffix(".srt"), sub_path.with_suffix(".vtt")]
    for c in candidates:
        if c.exists() and c.stat().st_size > 0:
            project_service.update_layer_status(project_id, "subtitles", LayerStatus.ready, {
                "file": str(c),
                "synced_with": "audio",
            })
            return c

    project_service.update_layer_status(project_id, "subtitles", LayerStatus.error, {
        "error": "Los subtitulos se generan junto con el audio. Genera primero la capa de audio.",
    })
    return None


async def fetch_pexels_clips(query: str, count: int = 8) -> list:
    pexels_key = os.getenv("PEXELS_API_KEY")
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": pexels_key},
            params={"query": query, "per_page": count, "orientation": "portrait"},
            timeout=15,
        )
    data = resp.json()
    urls = []
    for v in data.get("videos", []):
        for f in v.get("video_files", []):
            if f.get("quality") == "hd":
                urls.append(f["link"])
                break
    return urls


async def assemble_video_layer(project_id: str, config: ProjectConfig) -> Path:
    project_service.update_layer_status(project_id, "video", LayerStatus.pending)
    output_path = project_service.get_layer_path(project_id, "video")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    clips = []

    if config.video.source in (VideoSource.local, VideoSource.mixed):
        clips_dir = Path(config.video.local_folder or LOCAL_CLIPS_DIR)
        if clips_dir.exists():
            clips = list(clips_dir.glob("*.mp4"))[:8]

    if config.video.source in (VideoSource.pexels, VideoSource.mixed) and len(clips) < 8:
        pexels_query = " ".join(config.topic.split()[:4])
        pexels_urls = await fetch_pexels_clips(pexels_query, 8 - len(clips))
        dl_dir = Path("projects") / project_id / "video" / "downloads"
        dl_dir.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient() as client:
            for i, url in enumerate(pexels_urls):
                dest = dl_dir / f"pexels_{i}.mp4"
                r = await client.get(url, timeout=30, follow_redirects=True)
                dest.write_bytes(r.content)
                clips.append(dest)

    if not clips:
        project_service.update_layer_status(project_id, "video", LayerStatus.error, {
            "error": "No clips found"
        })
        return None

    norm_dir = Path("projects") / project_id / "video" / "normalized"
    norm_dir.mkdir(parents=True, exist_ok=True)
    norm_clips = []
    for i, c in enumerate(clips):
        norm = norm_dir / f"norm_{i:02d}.mp4"
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", str(c),
            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,"
                   "crop=1080:1920,setsar=1",
            "-r", "30",
            "-c:v", "libx264", "-crf", "23", "-preset", "fast",
            "-pix_fmt", "yuv420p",
            "-an",
            str(norm),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        if norm.exists() and norm.stat().st_size > 0:
            norm_clips.append(norm)

    if not norm_clips:
        project_service.update_layer_status(project_id, "video", LayerStatus.error, {
            "error": "Clip normalization failed"
        })
        return None

    list_file = Path("projects") / project_id / "video" / "clips.txt"
    list_file.write_text("\n".join(f"file '{c.resolve()}'" for c in norm_clips))

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        "-movflags", "+faststart",
        str(output_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    project_service.update_layer_status(project_id, "video", LayerStatus.ready, {
        "clips": len(norm_clips),
        "source": config.video.source,
        "file": str(output_path),
    })
    return output_path
