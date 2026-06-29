import os
import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger(__name__)


async def generate_edge_tts(text: str, voice: str, speed: float = 1.0) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp_path = tmp.name

    rate = f"+{int((speed - 1) * 100)}%" if speed >= 1 else f"{int((speed - 1) * 100)}%"
    proc = await asyncio.create_subprocess_exec(
        "edge-tts", "--voice", voice, "--rate", rate,
        "--text", text, "--write-media", tmp_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    conv_path = tmp_path + ".conv.mp3"
    conv = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-i", tmp_path,
        "-acodec", "libmp3lame", "-q:a", "2",
        conv_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await conv.communicate()

    result = Path(conv_path).read_bytes()
    Path(tmp_path).unlink(missing_ok=True)
    Path(conv_path).unlink(missing_ok=True)
    return result


async def generate_openai_tts(text: str, voice: str = "onyx", speed: float = 1.0) -> bytes:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise ValueError("OPENAI_API_KEY not set")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.openai.com/v1/audio/speech",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "tts-1-hd",
                "input": text,
                "voice": voice,
                "response_format": "mp3",
                "speed": speed,
            },
            timeout=60,
        )
    if resp.status_code != 200:
        raise RuntimeError(f"OpenAI TTS error: {resp.status_code} {resp.text}")
    return resp.content


async def generate_elevenlabs_tts(
    text: str,
    voice_id: str = "21m00Tcm4TlvDq8ikWAM",
    speed: float = 1.0,
) -> bytes:
    key = os.getenv("ELEVENLABS_API_KEY")
    if not key:
        raise ValueError("ELEVENLABS_API_KEY not set")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            headers={
                "xi-api-key": key,
                "Content-Type": "application/json",
            },
            json={
                "text": text,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.75,
                    "speed": speed,
                },
            },
            timeout=60,
        )
    if resp.status_code != 200:
        raise RuntimeError(f"ElevenLabs error: {resp.status_code} {resp.text}")
    return resp.content


async def list_elevenlabs_voices() -> list:
    key = os.getenv("ELEVENLABS_API_KEY")
    if not key:
        return []
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.elevenlabs.io/v1/voices",
            headers={"xi-api-key": key},
            timeout=15,
        )
    if resp.status_code != 200:
        return []
    data = resp.json()
    return [
        {
            "voice_id": v["voice_id"],
            "name": v["name"],
            "category": v.get("category", ""),
            "labels": v.get("labels", {}),
            "preview_url": v.get("preview_url"),
        }
        for v in data.get("voices", [])
    ]


async def generate_preview(
    provider: str,
    text: Optional[str] = None,
    voice: str = "es-ES-AlvaroNeural",
    voice_id: Optional[str] = None,
    speed: float = 1.0,
) -> bytes:
    preview_text = (text or "Esta es una vista previa de cómo sonará la narración del vídeo.")[:200]

    if provider == "edge":
        return await generate_edge_tts(preview_text, voice, speed)
    elif provider == "openai":
        return await generate_openai_tts(preview_text, voice, speed)
    elif provider == "elevenlabs":
        vid = voice_id or "21m00Tcm4TlvDq8ikWAM"
        return await generate_elevenlabs_tts(preview_text, vid, speed)
    else:
        raise ValueError(f"Unknown TTS provider: {provider}")


async def generate_full(
    provider: str,
    text: str,
    output_path: Path,
    voice: str = "es-ES-AlvaroNeural",
    voice_id: Optional[str] = None,
    speed: float = 1.0,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if provider == "edge":
        audio_bytes = await generate_edge_tts(text, voice, speed)
    elif provider == "openai":
        audio_bytes = await generate_openai_tts(text, voice, speed)
    elif provider == "elevenlabs":
        vid = voice_id or "21m00Tcm4TlvDq8ikWAM"
        audio_bytes = await generate_elevenlabs_tts(text, vid, speed)
    else:
        raise ValueError(f"Unknown TTS provider: {provider}")

    output_path.write_bytes(audio_bytes)
    return output_path
