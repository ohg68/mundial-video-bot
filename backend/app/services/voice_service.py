"""
Transcripción de voz (Whisper local vía faster-whisper) + extracción de
intención (DeepSeek). Usado por el bot de Telegram para comandos de voz.

Whisper local corre en el propio servidor — no depende de APIs externas, así
que es inmune a bloqueos geográficos. El modelo se descarga una vez de
HuggingFace y se cachea en memoria.
"""
import asyncio
import json
import logging
import os
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

# Modelo Whisper: tiny/base/small/medium. "base" es buen balance velocidad/calidad
# en CPU para español. Configurable con WHISPER_MODEL.
_WHISPER_SIZE = os.getenv("WHISPER_MODEL", "base")
_model = None


def _get_model():
    """Carga perezosa del modelo Whisper (singleton). Bloqueante: usar en thread."""
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        log.info(f"[voice] cargando modelo Whisper '{_WHISPER_SIZE}' (CPU, int8)...")
        _model = WhisperModel(_WHISPER_SIZE, device="cpu", compute_type="int8")
        log.info("[voice] modelo Whisper listo")
    return _model


async def transcribe(audio_path: Path) -> str:
    """Transcribe un archivo de audio a texto con Whisper local.
    El trabajo pesado corre en un thread para no bloquear el event loop."""
    def _run() -> str:
        model = _get_model()
        segments, _info = model.transcribe(
            str(audio_path), language="es", beam_size=1,
        )
        return " ".join(seg.text for seg in segments).strip()

    text = await asyncio.to_thread(_run)
    log.info(f"[voice] transcrito: {text[:80]!r}")
    return text


async def extract_intent(text: str) -> dict:
    """Interpreta el texto transcrito y extrae la intención del usuario.
    Devuelve un dict con 'action' y, según el caso, campos adicionales
    (title/topic/source para "create"; "layer" para "regenerate"/"request_upload").

    Acciones posibles (todas salvo "create" operan sobre el proyecto más
    reciente del chat, porque un ID de proyecto no se puede dictar de forma confiable):
      - "create": crear un video nuevo (title, topic, source)
      - "list": listar proyectos
      - "status": estado del proyecto más reciente
      - "render": renderizar el proyecto más reciente
      - "download": descargar el render del proyecto más reciente
      - "regenerate": regenerar una capa (layer: audio|video|subtitles)
      - "regenerate_script": regenerar el guión
      - "request_upload": el usuario quiere poner música u overlay (layer: music|overlay)
      - "unknown": no se entendió
    """
    deepseek_key = os.getenv("DEEPSEEK_API_KEY")
    if not deepseek_key:
        # Sin LLM, asumimos que todo es creación de video con el texto como tema
        return {"action": "create", "title": text[:60], "topic": text, "source": "pexels"}

    prompt = f"""Eres el intérprete de comandos de voz de LayerCut, un bot que genera videos cortos.

El usuario dijo por voz:
"{text}"

Determina su intención y responde SOLO con un JSON válido (sin markdown, sin explicación):

Si quiere CREAR un video nuevo:
{{"action": "create", "title": "<título corto y atractivo>", "topic": "<descripción del contenido>", "source": "<photos|pexels|mixed_photos>"}}

Reglas para "source":
- "photos" si menciona fotos/imágenes/fotografías
- "mixed_photos" si menciona mezcla/mix de fotos y video
- "pexels" si menciona videos/clips, o por defecto

Si quiere VER/LISTAR sus proyectos:
{{"action": "list"}}

Si pregunta por el estado/progreso de su video (ej: "¿cómo va mi video?", "¿ya está listo?"):
{{"action": "status"}}

Si pide renderizar o generar el video final (ej: "renderizalo", "generá el video"):
{{"action": "render"}}

Si pide que le mandes/descargues el video (ej: "mandame el video", "descargalo"):
{{"action": "download"}}

Si pide regenerar/rehacer una capa específica (ej: "cambiá el audio", "rehacé el video"):
{{"action": "regenerate", "layer": "<audio|video|subtitles>"}}

Si pide regenerar el guión (ej: "rehacé el guión", "cambiá el texto"):
{{"action": "regenerate_script"}}

Si pide agregar/poner música o un logo/overlay (ej: "ponle música", "agregale mi logo"):
{{"action": "request_upload", "layer": "<music|overlay>"}}

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
