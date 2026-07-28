"""Asistente conversacional de edición (chat con DeepSeek antes de renderizar)."""
from fastapi import APIRouter, HTTPException, Body
from app.services import ai_editor_service, project_service

router = APIRouter()


@router.get("/status")
def status():
    import os
    return {"configured": bool(os.getenv("DEEPSEEK_API_KEY"))}


@router.get("/{project_id}/history")
def get_history(project_id: str):
    history = ai_editor_service.get_history(project_id)
    # Solo turnos de texto de user/assistant — el frontend no necesita el
    # system prompt ni los mensajes de tool_calls/tool.
    out = []
    for msg in history:
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        content = msg.get("content")
        if isinstance(content, str) and content:
            out.append({"role": role, "text": content})
    return {"history": out}


@router.delete("/{project_id}/history")
def clear_history(project_id: str):
    ai_editor_service.clear_history(project_id)
    return {"status": "cleared"}


@router.post("/{project_id}/chat")
async def chat(project_id: str, body: dict = Body(...)):
    if not project_service.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")

    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message requerido")

    model = body.get("model")  # opcional, default "deepseek-chat"

    try:
        result = await ai_editor_service.chat(project_id, message, model=model)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Error del asistente: {e}")

    return result
