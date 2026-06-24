"""
Transcripción de voz (Groq Whisper) + extracción de intención (DeepSeek).
Usado por el bot de Telegram para crear videos por comandos de voz.
"""
import json
import logging
import os
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

GROQ_TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3"


async def transcribe(audio_path: Path) -> str:
    """Transcribe un archivo de audio a texto con Groq Whisper.
    Devuelve el texto transcrito, o lanza RuntimeError si falla."""
    key = os.getenv("GROQ_API_KEY")
    if not key:
        raise RuntimeError("GROQ_API_KEY no configurada en Railway")

    with open(audio_path, "rb") as f:
        files = {"file": (audio_path.name, f, "audio/ogg")}
        data = {"model": GROQ_MODEL, "language": "es", "response_format": "json"}
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                GROQ_TRANSCRIBE_URL,
                headers={"Authorization": f"Bearer {key}"},
                files=files,
                data=data,
            )

    if resp.status_code != 200:
        raise RuntimeError(f"Groq Whisper HTTP {resp.status_code}: {resp.text[:200]}")

    text = resp.json().get("text", "").strip()
    log.info(f"[voice] transcrito: {text[:80]!r}")
    return text


async def extract_intent(text: str) -> dict:
    """Interpreta el texto transcrito y extrae la intención del usuario.
    Devuelve un dict con 'action' y, según el caso, title/topic/source/project_id.

    Acciones posibles:
      - "create": crear un video nuevo (title, topic, source)
      - "list": listar proyectos
      - "unknown": no se entendió
    """
    deepseek_key = os.getenv("DEEPSEEK_API_KEY")
    if not deepseek_key:
        # Sin LLM, asumimos que todo es creación de video con el texto como tema
        return {"action": "create", "title": text[:60], "topic": text, "source": "pexels"}

    prompt = f"""Eres el intérprete de comandos de voz de LayerCut, un bot que genera videos del Mundial 2026.

El usuario dijo por voz:
"{text}"

Determina su intención y responde SOLO con un JSON válido (sin markdown, sin explicación):

Si quiere CREAR un video:
{{"action": "create", "title": "<título corto y atractivo>", "topic": "<descripción del contenido>", "source": "<photos|pexels|mixed_photos>"}}

Reglas para "source":
- "photos" si menciona fotos/imágenes/fotografías
- "mixed_photos" si menciona mezcla/mix de fotos y video
- "pexels" si menciona videos/clips, o por defecto

Si quiere VER/LISTAR sus proyectos:
{{"action": "list"}}

Si no se entiende:
{{"action": "unknown"}}"""

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.deepseek.com/chat/completions",
                headers={
                    "Authorization": f"Bearer {deepseek_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "deepseek-chat",
                    "max_tokens": 300,
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"},
                },
            )
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        intent = json.loads(content)
        log.info(f"[voice] intención: {intent}")
        return intent
    except Exception as e:
        log.warning(f"[voice] error extrayendo intención: {e}")
        # Fallback: tratar todo como creación
        return {"action": "create", "title": text[:60], "topic": text, "source": "pexels"}
