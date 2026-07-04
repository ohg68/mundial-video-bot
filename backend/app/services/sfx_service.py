"""Generación de foleys (efectos de sonido) con la API de ElevenLabs.

Texto -> mp3 (44.1 kHz). Requiere ELEVENLABS_API_KEY en el entorno.
Los foleys se guardan en projects/{id}/audio/sfx/ y se pueden descargar
para colocarlos en la edición; no entran solos al render.
"""

import os
import re
from pathlib import Path

import httpx

ELEVENLABS_SFX_URL = "https://api.elevenlabs.io/v1/sound-generation"


def sfx_dir(project_id: str) -> Path:
    return Path("projects") / project_id / "audio" / "sfx"


def _slug(texto: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", texto.lower()).strip("_")
    return s[:60] or "foley"


async def generate_sfx(project_id: str, prompt: str, duration: float | None = None) -> Path:
    """Genera un foley y lo guarda en el proyecto. Devuelve la ruta del mp3."""
    key = os.getenv("ELEVENLABS_API_KEY")
    if not key:
        raise RuntimeError("Falta ELEVENLABS_API_KEY en el entorno")

    payload: dict = {"text": prompt, "prompt_influence": 0.3}
    if duration:
        payload["duration_seconds"] = duration

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            ELEVENLABS_SFX_URL,
            headers={"xi-api-key": key, "Content-Type": "application/json"},
            json=payload,
            timeout=120,
        )
    if resp.status_code != 200:
        raise RuntimeError(f"ElevenLabs error (status {resp.status_code}): {resp.text[:300]}")

    out_dir = sfx_dir(project_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = _slug(prompt)
    dest = out_dir / f"{base}.mp3"
    n = 2
    while dest.exists():
        dest = out_dir / f"{base}_{n}.mp3"
        n += 1
    dest.write_bytes(resp.content)
    return dest
