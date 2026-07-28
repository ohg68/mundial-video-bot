"""Asistente conversacional para editar un proyecto ANTES de renderizar.

Usa DeepSeek (API compatible con function-calling estilo OpenAI), mismo
proveedor que ya usa `editing_service.py` — sin dependencia nueva, la key
ya está configurada en Railway.

Diseño pensado para moderar el gasto de tokens:
- Cada tool devuelve una confirmación corta, nunca el estado completo del
  proyecto — el modelo solo pide `get_project_state` cuando de verdad
  necesita mirar los valores actuales.
- El historial de la conversación vive en memoria por proyecto (no se
  reenvía el guion completo ni el transcript salvo que una tool lo pida
  explícitamente), así el tamaño del contexto no crece con el guion.
- Tope de mensajes de historial y de vueltas de tool-call por turno.
"""
import json
import logging
import os
from typing import Optional

import httpx

log = logging.getLogger(__name__)

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"
MAX_HISTORY_MESSAGES = 20  # trunca conversaciones muy largas (moderación de tokens)
MAX_TOOL_ROUNDS = 6

SYSTEM_PROMPT = """Sos el asistente de edición de LayerCut, una herramienta que arma videos \
verticales para redes (guion + narración TTS + clips de video + música + subtítulos + overlay).

Ayudás al usuario, en español y en tono amigable y cercano (no técnico), a revisar su video \
y pedir cambios en lenguaje simple — tanto ANTES de renderizar como DESPUÉS de ver el \
resultado. No editás video directamente: cambiás campos de configuración, regenerás capas y \
disparás el render final usando las funciones disponibles.

Reglas:
- Actuá, no preguntes de más. Si el pedido es claro, aplicá el cambio con la función correcta \
y confirmá en una frase corta y cálida (nada de jerga técnica como "config" o "layer" — \
hablá de "la música", "el video", "los subtítulos").
- Usá get_project_state SOLO si necesitás saber un valor actual antes de decidir (por ejemplo, \
para saber si ya hay overlay antes de mezclarlo con algo). No la llames por rutina.
- Sé breve. Una o dos frases por respuesta, sin listas largas ni explicaciones de más.
- Cuando el usuario está revisando un video YA renderizado y pide un cambio (ej. "bajá la \
música", "el logo muy grande"), aplicá el cambio, regenerá la capa afectada si hace falta \
con regenerate_layer, y ofrecé volver a renderizar con render_final — pero solo disparalo si \
el usuario lo confirma o dice algo como "sí, volvé a hacerlo" / "renderizá de nuevo".
- Si el pedido no tiene una función que lo cubra (ej. "cambiá el guion a que hable de X"), \
decilo de forma simple y sugerí el lugar correcto de la interfaz.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_project_state",
            "description": "Devuelve el estado actual resumido del proyecto: fuente de video, "
                           "config de audio/música/subtítulos/overlay, y qué capas están "
                           "listas. No incluye el guion completo. Llamala solo si necesitás "
                           "ver un valor antes de decidir un cambio.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_video_config",
            "description": "Cambia la configuración de la capa de video: fuente de clips, "
                           "duración de cada corte, modo A/B split.",
            "parameters": {
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
    },
    {
        "type": "function",
        "function": {
            "name": "update_audio_config",
            "description": "Cambia la configuración de la narración: voz, velocidad, volumen, "
                           "proveedor de TTS.",
            "parameters": {
                "type": "object",
                "properties": {
                    "voice": {"type": "string"},
                    "speed": {"type": "number",
                              "description": "1.0 = normal, 1.2 = 20% más rápido"},
                    "volume": {"type": "number", "description": "0.0 a 1.0"},
                    "tts_provider": {"type": "string",
                                     "enum": ["edge", "openai", "elevenlabs"]},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_music_config",
            "description": "Cambia la música de fondo: volumen y fundidos de entrada/salida.",
            "parameters": {
                "type": "object",
                "properties": {
                    "volume": {"type": "number", "description": "0.0 a 1.0"},
                    "fade_in": {"type": "number", "description": "segundos"},
                    "fade_out": {"type": "number", "description": "segundos"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_subtitles_config",
            "description": "Cambia el estilo de los subtítulos: fuente, tamaño, color, posición.",
            "parameters": {
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
    },
    {
        "type": "function",
        "function": {
            "name": "update_overlay_config",
            "description": "Cambia la posición y opacidad del logo/overlay.",
            "parameters": {
                "type": "object",
                "properties": {
                    "logo_position": {"type": "string", "enum": [
                        "top-left", "top-right", "bottom-left", "bottom-right"]},
                    "logo_opacity": {"type": "number", "description": "0.0 a 1.0"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "regenerate_layer",
            "description": "Regenera una capa desde cero con la config actual (tarda; puede "
                           "ser lento para video). Usala después de cambiar un parámetro que "
                           "requiere reprocesar, o si el usuario pide explícitamente "
                           "'regenerá X'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "layer": {"type": "string", "enum": ["audio", "video", "subtitles"]},
                },
                "required": ["layer"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_overlay_background",
            "description": "Quita el fondo de la imagen de overlay/logo actual, dejándola con "
                           "transparencia. Requiere que ya haya un overlay subido.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "render_final",
            "description": "Compone el video final con la configuración y capas actuales "
                           "(tarda 1-2 minutos). Usala cuando el usuario confirma que quiere "
                           "ver el resultado con los cambios aplicados, típicamente después de "
                           "ajustar algo y regenerar la capa correspondiente.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
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
    from app.services import project_service, layer_service, bg_removal_service, render_service
    from app.models.project import LayerStatus

    if name == "get_project_state":
        meta = project_service.get_project(project_id)
        if not meta:
            return "Error: proyecto no encontrado."
        return json.dumps(_summarize_state(meta), ensure_ascii=False)

    _LAYER_CONFIG_TOOLS = {
        "update_video_config": "video",
        "update_audio_config": "audio",
        "update_music_config": "music",
        "update_subtitles_config": "subtitles",
        "update_overlay_config": "overlay",
    }
    if name in _LAYER_CONFIG_TOOLS:
        layer = _LAYER_CONFIG_TOOLS[name]
        meta = project_service.get_project(project_id)
        if not meta:
            return "Error: proyecto no encontrado."
        # Merge, no reemplazo: no perder campos que la tool no tocó (ej. si
        # solo cambiás volumen, no borrar fade_in/fade_out ya seteados).
        merged = {**meta["config"].get(layer, {}), **tool_input}
        project_service.update_project_config(project_id, {layer: merged})
        return f"Config de {layer} actualizada: {tool_input}"

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

    if name == "render_final":
        try:
            output = await render_service.render_final(project_id)
        except Exception as e:
            return f"Error al renderizar: {e}"

        from app.api.render import _save_to_history
        _save_to_history(project_id, output, "full")

        from app.services import cloud_storage
        await cloud_storage.upload_render(project_id, output)
        await cloud_storage.backup_db()

        project_service.update_layer_status(project_id, "video", LayerStatus.ready, {
            "output": str(output),
        })
        return "Video renderizado. Ya podés verlo y descargarlo."

    return f"Error: función desconocida '{name}'."


# Historial de chat en memoria, por proyecto. Se resetea en cada deploy/restart —
# es una sesión de trabajo previa al render, no un registro permanente.
_HISTORY: dict[str, list] = {}


def get_history(project_id: str) -> list:
    return _HISTORY.get(project_id, [])


def clear_history(project_id: str):
    _HISTORY.pop(project_id, None)


async def chat(project_id: str, message: str, model: Optional[str] = None) -> dict:
    """Un turno de conversación. Ejecuta el loop de function-calling contra
    DeepSeek y devuelve la respuesta final + qué tools se llamaron (para
    refrescar la UI)."""
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY no configurado")

    model = model or DEFAULT_MODEL

    history = _HISTORY.setdefault(project_id, [])
    if not history:
        history.append({"role": "system", "content": SYSTEM_PROMPT})
    history.append({"role": "user", "content": message})

    # Moderación de tokens: no dejar crecer el historial sin límite
    # (siempre conservando el system prompt en la posición 0).
    if len(history) > MAX_HISTORY_MESSAGES:
        del history[1: len(history) - MAX_HISTORY_MESSAGES + 1]

    actions_taken = []
    final_text = ""

    async with httpx.AsyncClient(timeout=40) as client:
        for _ in range(MAX_TOOL_ROUNDS):
            resp = await client.post(
                DEEPSEEK_URL,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": history,
                    "tools": TOOLS,
                    "max_tokens": 800,
                },
            )
            data = resp.json()
            if resp.status_code != 200 or "choices" not in data:
                raise RuntimeError(f"DeepSeek error ({resp.status_code}): {data}")

            msg = data["choices"][0]["message"]
            history.append(msg)

            tool_calls = msg.get("tool_calls")
            if not tool_calls:
                final_text = msg.get("content") or ""
                break

            for call in tool_calls:
                fn = call["function"]
                try:
                    tool_input = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    tool_input = {}
                result = await _execute_tool(project_id, fn["name"], tool_input)
                actions_taken.append({"tool": fn["name"], "input": tool_input})
                history.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": result,
                })
        else:
            final_text = "Se hicieron varios cambios seguidos — decime si necesitás algo más."

    return {"reply": final_text, "actions": actions_taken}
