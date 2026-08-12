import os
import logging
from typing import Optional

import httpx

from app.services.script_utils import clean_script

log = logging.getLogger(__name__)

from app.services.script_utils import fit_to_duration, target_words

# Cada plantilla es una lista de (bloque, peso). Los pesos se reparten sobre la
# duración que se pida, así que la misma plantilla sirve para 30, 60 o 90 s.
# Antes los segundos estaban escritos a mano ("Puesto 5 (12s)") y sumaban ~80:
# con una duración distinta el prompt se contradecía a sí mismo y el modelo
# seguía los segundos del desglose en vez del total.
TEMPLATE_HEADLINES = {
    "free": "",
    "preview": "Genera un guion para un vídeo de PREVIA de partido.",
    "summary": "Genera un guion para un RESUMEN post-partido.",
    "top5": "Genera un guion tipo TOP 5 / ranking.",
    "tutorial": "Genera un guion tipo TUTORIAL / explicación.",
}

TEMPLATE_BEATS = {
    "free": [],
    "preview": [
        ("Gancho dramático", 5), ("Contexto del partido", 15),
        ("Estado de los equipos", 20), ("Jugadores clave", 20),
        ("Predicción", 15), ("Llamada a la acción", 5),
    ],
    "summary": [
        ("Resultado y reacción", 5), ("Primer tiempo — jugadas clave", 25),
        ("Segundo tiempo — goles y momentos", 25), ("MVP del partido", 10),
        ("Qué viene después", 10), ("Llamada a la acción", 5),
    ],
    "top5": [
        ("Intro + qué se va a rankear", 5), ("Puesto 5", 12), ("Puesto 4", 12),
        ("Puesto 3", 12), ("Puesto 2", 12), ("Puesto 1 — con buildup", 15),
        ("Cierre + CTA", 7),
    ],
    "tutorial": [
        ("Problema o pregunta", 5), ("Contexto breve", 10), ("Paso 1", 15),
        ("Paso 2", 15), ("Paso 3", 15), ("Resultado / resumen", 10),
        ("Llamada a la acción", 5),
    ],
}

TEMPLATE_NAMES = {
    "free": "Libre",
    "preview": "Previa de partido",
    "summary": "Resumen post-partido",
    "top5": "Top 5 / Ranking",
    "tutorial": "Tutorial / Explicación",
}


def _render_template(template: str, target_seconds: int) -> str:
    """Instrucciones de la plantilla con los segundos escalados a la duración pedida."""
    beats = TEMPLATE_BEATS.get(template) or []
    if not beats:
        return ""

    total_peso = sum(p for _, p in beats)
    lineas = []
    for i, (bloque, peso) in enumerate(beats, 1):
        # Mínimo 2 s por bloque: en un video de 30 s un bloque de peso 5 sobre 80
        # daría menos de 2 s, que no alcanza ni para una frase.
        segundos = max(2, round(peso / total_peso * target_seconds))
        lineas.append(f"{i}. {bloque} ({segundos}s)")

    return "{}\nEstructura:\n{}".format(TEMPLATE_HEADLINES.get(template, ""), "\n".join(lineas))


def _build_prompt(topic: str, template: str, language: str, match: str = None,
                  match_date: str = None, target_seconds: int = 60) -> str:
    template_instructions = _render_template(template, target_seconds)
    palabras = target_words(target_seconds)

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

EXTENSIÓN (lo más importante): exactamente {palabras} palabras, con un margen del
10% arriba o abajo. El guion se lee en voz alta y tiene que durar
{target_seconds} segundos. Ajustá la profundidad de cada bloque a ese
presupuesto: es mejor decir menos cosas y decirlas bien que meter todo y pasarse.

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
    target_seconds: int = 60,
) -> str:
    prompt = _build_prompt(topic, template, language, match, match_date, target_seconds)

    if provider == "deepseek":
        raw = await generate_deepseek(prompt)
    elif provider == "claude":
        raw = await generate_claude(prompt)
    elif provider == "openai":
        raw = await generate_openai(prompt)
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")

    script = clean_script(raw)  # quitar encabezados/acotaciones del LLM
    # Red de seguridad: pedir palabras acierta mucho más que pedir segundos, pero
    # los modelos igual se pasan. Sin esto, elegir 30 s daba videos de 50.
    return fit_to_duration(script, target_words(target_seconds))


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
            "name": TEMPLATE_NAMES[k],
            # Los bloques, sin segundos: ahora dependen de la duración elegida.
            "description": " · ".join(b for b, _ in beats) if beats else "Sin estructura predefinida",
        }
        for k, beats in TEMPLATE_BEATS.items()
    }
