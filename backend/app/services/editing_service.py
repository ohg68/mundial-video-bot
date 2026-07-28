import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger(__name__)

DEFAULTS = {
    "marketing": {
        "default_shot_duration": 2.5,
        "pacing_curve": "constant",
        "transitions": ["cut"],
        "effects": ["zoom_in", "text_overlay"],
        "description": "Cortes rapidos, ritmo constante, CTAs claros",
    },
    "music": {
        "default_shot_duration": 0,
        "pacing_curve": "build_climax_outro",
        "transitions": ["xfade"],
        "effects": ["color_pulse", "beat_sync"],
        "description": "Ritmo sincronizado al BPM, transiciones suaves, visual variable",
    },
    "sports": {
        "default_shot_duration": 2.0,
        "pacing_curve": "energy_peaks",
        "transitions": ["cut", "whip"],
        "effects": ["slow_mo", "replay"],
        "description": "Highlights rapidos, slow-motion en momentos clave, energia alta",
    },
}


def get_defaults(video_type: str) -> dict:
    d = DEFAULTS.get(video_type, DEFAULTS["marketing"]).copy()
    d["video_type"] = video_type
    return d


def _build_editing_prompt(
    video_type: str,
    editing_prompt: str,
    total_duration: float,
    bpm: Optional[int] = None,
) -> str:
    defaults = DEFAULTS.get(video_type, DEFAULTS["marketing"])

    bpm_info = ""
    if video_type == "music" and bpm:
        beat_dur = round(60 / bpm, 2)
        bpm_info = f"""
BPM del track: {bpm}
Duracion de un beat: {beat_dur}s
Los cortes deben caer en beats o cada 2/4 beats para que el video siga el ritmo de la musica."""

    return f"""Eres un editor de video profesional para YouTube.

Genera un plan de edicion en JSON para un video tipo {video_type.upper()}.

Duracion total del video: {total_duration} segundos
Tipo: {video_type} — {defaults['description']}
{bpm_info}

Instrucciones del usuario sobre el ritmo:
"{editing_prompt}"

El plan debe ser un JSON con esta estructura exacta:
{{
  "video_type": "{video_type}",
  "total_duration": {total_duration},
  "bpm": {bpm or 'null'},
  "pacing_curve": "{defaults['pacing_curve']}",
  "default_shot_duration": <float>,
  "segments": [
    {{
      "start": <float>,
      "end": <float>,
      "transition": "cut" | "xfade" | "fade",
      "xfade_duration": <float o null>,
      "effect": "zoom_in" | "zoom_out" | "slow_mo" | "color_pulse" | "beat_sync" | null,
      "intensity": <float 0.0-1.0>
    }}
  ]
}}

Reglas:
- Los segmentos deben cubrir toda la duracion sin gaps
- El primer segmento empieza en 0
- El ultimo segmento termina en {total_duration}
- Para "xfade", incluir xfade_duration (0.3-1.0s)
- La intensidad indica la fuerza del efecto (0=sutil, 1=maximo)
- Para video musical: los cortes deben caer en beats del BPM
- Para marketing: ritmo constante, cortes cada 2-3 segundos
- Para deportes: variar entre cortes rapidos (1.5s) y pausas (4s)

Responde SOLO con el JSON, sin explicacion."""


async def generate_editing_plan(
    video_type: str,
    editing_prompt: str,
    total_duration: float = 90.0,
    bpm: Optional[int] = None,
    provider: str = "deepseek",
) -> dict:
    prompt = _build_editing_prompt(video_type, editing_prompt, total_duration, bpm)

    if provider == "deepseek":
        raw = await _call_deepseek(prompt)
    elif provider == "claude":
        raw = await _call_claude(prompt)
    elif provider == "openai":
        raw = await _call_openai(prompt)
    else:
        raise ValueError(f"Unknown provider: {provider}")

    plan = _parse_json(raw)
    plan = validate_plan(plan, total_duration)
    return plan


async def _call_deepseek(prompt: str) -> str:
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        raise ValueError("DEEPSEEK_API_KEY not set")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "max_tokens": 2000,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=40,
        )
    data = resp.json()
    if resp.status_code != 200 or "choices" not in data:
        raise RuntimeError(f"DeepSeek error ({resp.status_code}): {data}")
    return data["choices"][0]["message"]["content"]


async def _call_claude(prompt: str) -> str:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError("ANTHROPIC_API_KEY not set")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "Content-Type": "application/json"},
            json={"model": "claude-sonnet-4-20250514", "max_tokens": 2000,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=40,
        )
    data = resp.json()
    if resp.status_code != 200:
        raise RuntimeError(f"Claude error ({resp.status_code}): {data}")
    return data["content"][0]["text"]


async def _call_openai(prompt: str) -> str:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise ValueError("OPENAI_API_KEY not set")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": "gpt-4o-mini", "max_tokens": 2000,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=40,
        )
    data = resp.json()
    if resp.status_code != 200 or "choices" not in data:
        raise RuntimeError(f"OpenAI error ({resp.status_code}): {data}")
    return data["choices"][0]["message"]["content"]


def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    for start_char in ("{", "["):
        idx = raw.find(start_char)
        if idx >= 0:
            try:
                obj, _ = json.JSONDecoder().raw_decode(raw, idx)
                return obj if isinstance(obj, dict) else {"segments": obj}
            except json.JSONDecodeError:
                continue
    raise ValueError(f"Could not parse JSON from LLM response: {raw[:200]}")


def validate_plan(plan: dict, total_duration: float) -> dict:
    plan.setdefault("total_duration", total_duration)
    plan.setdefault("pacing_curve", "constant")
    plan.setdefault("default_shot_duration", 3.0)

    segments = plan.get("segments", [])
    if not segments:
        n = max(1, round(total_duration / plan["default_shot_duration"]))
        seg_dur = total_duration / n
        segments = [
            {"start": round(i * seg_dur, 2), "end": round((i + 1) * seg_dur, 2),
             "transition": "cut", "effect": None, "intensity": 0.5}
            for i in range(n)
        ]

    valid = []
    for s in segments:
        s["start"] = max(0.0, float(s.get("start", 0)))
        s["end"] = min(total_duration, float(s.get("end", total_duration)))
        if s["end"] <= s["start"]:
            continue
        s.setdefault("transition", "cut")
        if s["transition"] not in ("cut", "xfade", "fade"):
            s["transition"] = "cut"
        s.setdefault("xfade_duration", 0.5 if s["transition"] == "xfade" else None)
        s.setdefault("effect", None)
        s["intensity"] = max(0.0, min(1.0, float(s.get("intensity", 0.5))))
        valid.append(s)

    if valid:
        valid[0]["start"] = 0.0
        valid[-1]["end"] = total_duration

    plan["segments"] = valid
    return plan


def plan_to_shot_durations(plan: dict) -> list[float]:
    return [round(s["end"] - s["start"], 2) for s in plan.get("segments", [])]


async def build_xfade_chain(
    shot_files: list[Path],
    segments: list[dict],
    dest: Path,
) -> Optional[Path]:
    if len(shot_files) < 2:
        return shot_files[0] if shot_files else None

    has_xfade = any(s.get("transition") == "xfade" for s in segments)
    if not has_xfade:
        return None

    inputs = []
    for f in shot_files:
        inputs += ["-i", str(f)]

    filter_parts = []
    prev_label = "[0:v]"
    offset = 0.0

    for i in range(1, len(shot_files)):
        seg = segments[i] if i < len(segments) else {}
        transition = seg.get("transition", "cut")
        xfade_dur = float(seg.get("xfade_duration", 0.5))
        shot_dur = segments[i - 1]["end"] - segments[i - 1]["start"] if i - 1 < len(segments) else 3.0

        out_label = f"[vx{i}]"

        if transition == "xfade":
            offset += shot_dur - xfade_dur
            filter_parts.append(
                f"{prev_label}[{i}:v]xfade=transition=fade:duration={xfade_dur}:offset={offset:.2f}{out_label}"
            )
        else:
            offset += shot_dur
            filter_parts.append(
                f"{prev_label}[{i}:v]xfade=transition=fade:duration=0.05:offset={offset:.2f}{out_label}"
            )

        prev_label = out_label

    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", ";".join(filter_parts),
        "-map", prev_label,
        "-c:v", "libx264", "-crf", "23", "-preset", "fast", "-an",
        str(dest),
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        log.warning(f"xfade chain failed: {stderr.decode()[-300:]}")
        return None

    return dest
