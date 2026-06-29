import os
import logging
from typing import Optional

import httpx

from app.services.script_utils import clean_script

log = logging.getLogger(__name__)

TEMPLATES = {
    "free": "",
    "preview": """Genera un guion para un vídeo de PREVIA de partido.
Estructura:
1. Gancho dramático (5s)
2. Contexto del partido (15s)
3. Estado de los equipos (20s)
4. Jugadores clave (20s)
5. Predicción (15s)
6. Llamada a la acción (5s)
Duración total: ~80 segundos leídos.""",

    "summary": """Genera un guion para un RESUMEN post-partido.
Estructura:
1. Resultado y reacción (5s)
2. Primer tiempo — jugadas clave (25s)
3. Segundo tiempo — goles y momentos (25s)
4. MVP del partido (10s)
5. Qué viene después (10s)
6. Llamada a la acción (5s)
Duración total: ~80 segundos leídos.""",

    "top5": """Genera un guion tipo TOP 5 / ranking.
Estructura:
1. Intro + qué se va a rankear (5s)
2. Puesto 5 (12s)
3. Puesto 4 (12s)
4. Puesto 3 (12s)
5. Puesto 2 (12s)
6. Puesto 1 — con buildup (15s)
7. Cierre + CTA (7s)
Duración total: ~75 segundos leídos.""",

    "tutorial": """Genera un guion tipo TUTORIAL / explicación.
Estructura:
1. Problema o pregunta (5s)
2. Contexto breve (10s)
3. Paso 1 (15s)
4. Paso 2 (15s)
5. Paso 3 (15s)
6. Resultado / resumen (10s)
7. Llamada a la acción (5s)
Duración total: ~75 segundos leídos.""",
}


def _build_prompt(topic: str, template: str, language: str, match: str = None, match_date: str = None) -> str:
    template_instructions = TEMPLATES.get(template, "")

    base = f"""Eres un locutor profesional para un canal de YouTube.
Genera un guion en {"español" if language == "es" else language} para un vídeo corto sobre:
Tema: {topic}
{f"Partido: {match}" if match else ""}
{f"Fecha: {match_date}" if match_date else ""}

{template_instructions}

El guion debe:
- Tener un gancho en los primeros 5 segundos
- Ser informativo y emocionante
- Terminar con una llamada a la acción
- Solo el texto que leerá el locutor, sin indicaciones de escena
- Separar cada bloque/párrafo con una línea en blanco

Responde SOLO con el guion, sin introducción ni explicación."""

    return base


async def generate_deepseek(prompt: str) -> str:
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        raise ValueError("DEEPSEEK_API_KEY not set")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.deepseek.com/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek-chat",
                "max_tokens": 1500,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
    data = resp.json()
    if resp.status_code != 200 or "choices" not in data:
        raise RuntimeError(f"DeepSeek error ({resp.status_code}): {data}")
    return data["choices"][0]["message"]["content"]


async def generate_claude(prompt: str) -> str:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError("ANTHROPIC_API_KEY not set")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 1500,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
    data = resp.json()
    if resp.status_code != 200:
        raise RuntimeError(f"Claude error ({resp.status_code}): {data}")
    return data["content"][0]["text"]


async def generate_openai(prompt: str) -> str:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise ValueError("OPENAI_API_KEY not set")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",
                "max_tokens": 1500,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
    data = resp.json()
    if resp.status_code != 200 or "choices" not in data:
        raise RuntimeError(f"OpenAI error ({resp.status_code}): {data}")
    return data["choices"][0]["message"]["content"]


async def generate_script(
    topic: str,
    provider: str = "deepseek",
    template: str = "free",
    language: str = "es",
    match: Optional[str] = None,
    match_date: Optional[str] = None,
) -> str:
    prompt = _build_prompt(topic, template, language, match, match_date)

    if provider == "deepseek":
        raw = await generate_deepseek(prompt)
    elif provider == "claude":
        raw = await generate_claude(prompt)
    elif provider == "openai":
        raw = await generate_openai(prompt)
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")

    return clean_script(raw)  # quitar encabezados/acotaciones del LLM


def estimate_timestamps(script: str, wpm: float = 150) -> list:
    paragraphs = [p.strip() for p in script.split("\n\n") if p.strip()]
    wps = wpm / 60
    blocks = []
    current_time = 0.0

    for p in paragraphs:
        word_count = len(p.split())
        duration = word_count / wps
        blocks.append({
            "text": p,
            "words": word_count,
            "start": round(current_time, 1),
            "end": round(current_time + duration, 1),
            "duration": round(duration, 1),
        })
        current_time += duration

    return blocks


def get_templates() -> dict:
    return {
        k: {
            "name": {
                "free": "Libre",
                "preview": "Previa de partido",
                "summary": "Resumen post-partido",
                "top5": "Top 5 / Ranking",
                "tutorial": "Tutorial / Explicación",
            }[k],
            "description": v[:80] + "..." if v else "Sin estructura predefinida",
        }
        for k, v in TEMPLATES.items()
    }
