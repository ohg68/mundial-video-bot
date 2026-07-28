"""Asistente conversacional para editar un proyecto ANTES de renderizar.

Diseño pensado para moderar el gasto de tokens:
- El modelo por defecto es Haiku (barato); Sonnet queda disponible para
  pedidos que el usuario marque como complejos.
- Cada tool devuelve una confirmación corta, nunca el estado completo del
  proyecto — el modelo solo pide `get_project_state` cuando de verdad
  necesita mirar los valores actuales.
- El historial de la conversación vive en memoria por proyecto (no se
  reenvía el guion completo ni el transcript salvo que una tool lo pida
  explícitamente), así el tamaño del contexto no crece con el guion.
"""
import json
import logging
import os
from typing import Optional

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-haiku-4-5"
MAX_HISTORY_MESSAGES = 20  # trunca conversaciones muy largas (moderación de tokens)

SYSTEM_PROMPT = """Sos el asistente de edición de LayerCut, una herramienta que arma videos \
verticales para redes (guion + narración TTS + clips de video + música + subtítulos + overlay).

Ayudás al usuario a ajustar la configuración de su proyecto ANTES de renderizar, por chat, \
en español. No editás video directamente: cambiás campos de configuración y disparás \
generación de capas cuando hace falta, usando las tools disponibles.

Reglas:
- Actuá, no preguntes de más. Si el pedido es claro, aplicá el cambio con la tool correcta \
y confirmá en una frase corta.
- Usá get_project_state SOLO si necesitás saber un valor actual antes de decidir (por ejemplo, \
para saber si ya hay overlay antes de mezclarlo con algo). No lo llames por rutina.
- Sé breve. Una o dos frases por respuesta, sin listas largas ni explicaciones de más.
- Si el pedido no tiene una tool que lo cubra (ej. "cambiá el guion a que hable de X"), decilo \
y sugerí el lugar correcto de la interfaz.
"""

TOOLS = [
    {
        "name": "get_project_state",
        "description": "Devuelve el estado actual resumido del proyecto: fuente de video, "
                       "config de audio/música/subtítulos/overlay, y qué capas están listas. "
                       "No incluye el guion completo. Llamala solo si necesitás ver un valor "
                       "antes de decidir un cambio.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "update_video_config",
        "description": "Cambia la configuración de la capa de video: fuente de clips, "
                       "duración de cada corte, modo A/B split.",
        "input_schema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "enum": [
                    "local", "pexels", "pixabay", "coverr", "youtube", "mixed",
                    "photos", "mixed_photos", "gdrive", "stock", "wikimedia"]},
                "clip_duration": {"type": "number", "description": "Segundos por corte"},
                "ab_split": {"type": "boolean"},
            },
            "required": [],
        },
    },
    {
        "name": "update_audio_config",
        "description": "Cambia la configuración de la narración: voz, velocidad, volumen, "
                       "proveedor de TTS.",
        "input_schema": {
            "type": "object",
            "properties": {
                "voice": {"type": "string"},
                "speed": {"type": "number", "description": "1.0 = normal, 1.2 = 20% más rápido"},
                "volume": {"type": "number", "description": "0.0 a 1.0"},
                "tts_provider": {"type": "string", "enum": ["edge", "openai", "elevenlabs"]},
            },
            "required": [],
        },
    },
    {
        "name": "update_music_config",
        "description": "Cambia la música de fondo: volumen y fundidos de entrada/salida.",
        "input_schema": {
            "type": "object",
            "properties": {
                "volume": {"type": "number", "description": "0.0 a 1.0"},
                "fade_in": {"type": "number", "description": "segundos"},
                "fade_out": {"type": "number", "description": "segundos"},
            },
            "required": [],
        },
    },
    {
        "name": "update_subtitles_config",
        "description": "Cambia el estilo de los subtítulos: fuente, tamaño, color, posición.",
        "input_schema": {
            "type": "object",
            "properties": {
                "font": {"type": "string"},
                "font_size": {"type": "number"},
                "color": {"type": "string"},
                "position": {"type": "string", "enum": ["top", "center", "bottom"]},
            },
            "required": [],
        },
    },
    {
        "name": "update_overlay_config",
        "description": "Cambia la posición y opacidad del logo/overlay.",
        "input_schema": {
            "type": "object",
            "properties": {
                "logo_position": {"type": "string", "enum": [
                    "top-left", "top-right", "bottom-left", "bottom-right"]},
                "logo_opacity": {"type": "number", "description": "0.0 a 1.0"},
            },
            "required": [],
        },
    },
    {
        "name": "regenerate_layer",
        "description": "Regenera una capa desde cero con la config actual (tarda; puede ser "
                       "lento para video). Usala después de cambiar un parámetro que requiere "
                       "reprocesar, o si el usuario pide explícitamente 'regenerá X'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "layer": {"type": "string", "enum": ["audio", "video", "subtitles"]},
            },
            "required": ["layer"],
        },
    },
    {
        "name": "remove_overlay_background",
        "description": "Quita el fondo de la imagen de overlay/logo actual, dejándola con "
                       "transparencia. Requiere que ya haya un overlay subido.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]


def _summarize_state(meta: dict) -> dict:
    config = meta.get("config", {})
    layers = meta.get("layers", {})
    return {
        "title": meta.get("title"),
        "layers_ready": {k: v for k, v in layers.items()},
        "video": {
            "source": config.get("video", {}).get("source"),
            "clip_duration": config.get("video", {}).get("clip_duration"),
            "ab_split": config.get("video", {}).get("ab_split"),
        },
        "audio": {
            "voice": config.get("audio", {}).get("voice"),
            "speed": config.get("audio", {}).get("speed"),
            "volume": config.get("audio", {}).get("volume"),
            "tts_provider": config.get("audio", {}).get("tts_provider"),
        },
        "music": config.get("music", {}),
        "subtitles": config.get("subtitles", {}),
        "overlay": config.get("overlay", {}),
    }


async def _execute_tool(project_id: str, name: str, tool_input: dict) -> str:
    """Ejecuta una tool y devuelve un string corto para el modelo (no JSON gigante)."""
    from app.services import project_service, layer_service, bg_removal_service
    from app.models.project import LayerStatus

    if name == "get_project_state":
        meta = project_service.get_project(project_id)
        if not meta:
            return "Error: proyecto no encontrado."
        return json.dumps(_summarize_state(meta), ensure_ascii=False)

    if name == "update_video_config":
        project_service.update_project_config(project_id, {"video": tool_input})
        return f"Config de video actualizada: {tool_input}"

    if name == "update_audio_config":
        project_service.update_project_config(project_id, {"audio": tool_input})
        return f"Config de audio actualizada: {tool_input}"

    if name == "update_music_config":
        project_service.update_project_config(project_id, {"music": tool_input})
        return f"Config de música actualizada: {tool_input}"

    if name == "update_subtitles_config":
        project_service.update_project_config(project_id, {"subtitles": tool_input})
        return f"Config de subtítulos actualizada: {tool_input}"

    if name == "update_overlay_config":
        project_service.update_project_config(project_id, {"overlay": tool_input})
        return f"Config de overlay actualizada: {tool_input}"

    if name == "regenerate_layer":
        layer = tool_input.get("layer")
        meta = project_service.get_project(project_id)
        if not meta:
            return "Error: proyecto no encontrado."
        from app.models.project import ProjectConfig
        config = ProjectConfig(**meta["config"])
        try:
            if layer == "audio":
                await layer_service.generate_audio(project_id, config)
            elif layer == "video":
                await layer_service.assemble_video_layer(project_id, config)
            elif layer == "subtitles":
                await layer_service.generate_subtitles(project_id, config)
            else:
                return f"Error: capa '{layer}' inválida."
        except Exception as e:
            return f"Error regenerando {layer}: {e}"

        from app.services import cloud_storage
        await cloud_storage.upload_layer(
            project_id, layer, project_service.get_layer_path(project_id, layer))
        return f"Capa '{layer}' regenerada."

    if name == "remove_overlay_background":
        overlay_path = project_service.get_layer_path(project_id, "overlay")
        if not overlay_path.exists():
            return "Error: no hay overlay subido todavía."
        tmp_out = overlay_path.with_suffix(".nobg.png")
        ok = await bg_removal_service.remove_background(overlay_path, tmp_out)
        if not ok:
            tmp_out.unlink(missing_ok=True)
            return "Error: no se pudo quitar el fondo."
        tmp_out.replace(overlay_path)
        project_service.update_layer_status(project_id, "overlay", LayerStatus.ready, {
            "source": "custom", "file": str(overlay_path), "bg_removed": True,
        })
        from app.services import cloud_storage
        await cloud_storage.upload_layer(project_id, "overlay", overlay_path)
        return "Fondo del overlay eliminado."

    return f"Error: tool desconocida '{name}'."


# Historial de chat en memoria, por proyecto. Se resetea en cada deploy/restart —
# es una sesión de trabajo previa al render, no un registro permanente.
_HISTORY: dict[str, list] = {}


def get_history(project_id: str) -> list:
    return _HISTORY.get(project_id, [])


def clear_history(project_id: str):
    _HISTORY.pop(project_id, None)


async def chat(project_id: str, message: str, model: Optional[str] = None) -> dict:
    """Un turno de conversación. Ejecuta el loop de tool-use y devuelve la
    respuesta final + qué tools se llamaron (para refrescar la UI)."""
    import anthropic

    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY no configurado")

    client = anthropic.AsyncAnthropic(api_key=key)
    model = model or DEFAULT_MODEL

    history = _HISTORY.setdefault(project_id, [])
    history.append({"role": "user", "content": message})
    # Moderación de tokens: no dejar crecer el historial sin límite.
    if len(history) > MAX_HISTORY_MESSAGES:
        del history[: len(history) - MAX_HISTORY_MESSAGES]

    actions_taken = []
    final_text = ""

    for _ in range(6):  # tope de vueltas de tool-use por turno
        response = await client.messages.create(
            model=model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=history,
        )

        history.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            final_text = "".join(
                b.text for b in response.content if getattr(b, "type", None) == "text"
            )
            break

        tool_results = []
        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            result = await _execute_tool(project_id, block.name, block.input)
            actions_taken.append({"tool": block.name, "input": block.input})
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result,
            })
        history.append({"role": "user", "content": tool_results})
    else:
        final_text = "Se hicieron varios cambios seguidos — decime si necesitás algo más."

    return {"reply": final_text, "actions": actions_taken}
