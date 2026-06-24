"""
Limpieza de guiones generados por LLM.

Los modelos a veces incluyen encabezados, acotaciones o metadata que no debe
narrarse ni aparecer en subtítulos, p. ej.:
    **TEXTO DEL NARRADOR (90 segundos):**
    NARRADOR:
    ## Gancho (5s)
    (Tono enérgico)
Esta función deja solo el texto realmente narrable.
"""
import re

# Líneas que son claramente indicaciones/metadata (se descartan por completo)
_INSTRUCTION_PREFIXES = (
    "texto del narrador", "narrador", "narrator", "locutor", "voz en off",
    "voiceover", "voice-over", "guion", "guión", "script", "titulo", "título",
    "title", "tema", "gancho", "intro", "introducción", "introduccion",
    "desarrollo", "cierre", "conclusión", "conclusion", "cta",
    "llamada a la acción", "llamada a la accion", "duración", "duracion",
    "escena", "nota", "instrucciones",
)

# (90 segundos), (5s), (00:15), (Tono enérgico), etc. al inicio o como línea sola
_TIME_PAREN = re.compile(r"^\s*\(?\s*\d+\s*(s|seg|segundos|min|minutos|:|\d)", re.IGNORECASE)
# Línea entera entre paréntesis o corchetes → acotación
_FULL_PAREN = re.compile(r"^\s*[\(\[].*[\)\]]\s*$")
# Markdown de encabezado: ##, ###, ---, ===
_MD_HEADER = re.compile(r"^\s*(#{1,6}\s|[-=]{3,}\s*$)")


def _strip_md(text: str) -> str:
    """Quita marcadores markdown inline (**, *, __, _, `) sin tocar el texto."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    return text


def _process_line(line: str):
    """Procesa una línea. Devuelve el texto narrable, o None si debe descartarse."""
    raw = line.strip()
    if not raw:
        return ""  # conservar línea en blanco (separador de párrafo)

    # Quitar markdown para evaluar el contenido real
    stripped = _strip_md(raw).strip()
    if not stripped:
        return None  # era solo "**", "##", etc.

    # Encabezados markdown / separadores
    if _MD_HEADER.match(raw):
        return None

    # Línea completa entre paréntesis/corchetes → acotación
    if _FULL_PAREN.match(stripped):
        return None

    # Empieza con marca de tiempo: "(90 segundos)", "5s -", "00:15"
    if _TIME_PAREN.match(stripped):
        return None

    low = stripped.lower()

    # "PREFIJO: ..." → si el prefijo es una indicación, quitarlo y evaluar el resto
    if ":" in stripped:
        head = low.split(":", 1)[0].split("(", 1)[0].strip()
        for pref in _INSTRUCTION_PREFIXES:
            if head == pref or head.startswith(pref + " ") or head == pref + ".":
                after = stripped.split(":", 1)[1].strip()
                if len(after.split()) >= 3:
                    return after          # conservar el contenido narrable tras el prefijo
                return None               # solo era encabezado de sección

    # Encabezado de sección con paréntesis y poco texto: "Gancho (5s)"
    head_paren = low.split("(", 1)[0].strip()
    if "(" in stripped:
        for pref in _INSTRUCTION_PREFIXES:
            if head_paren == pref or head_paren.startswith(pref + " "):
                return None

    # Línea TODA EN MAYÚSCULAS y corta → suele ser encabezado
    letters = [c for c in stripped if c.isalpha()]
    if letters and stripped.upper() == stripped and len(stripped.split()) <= 6:
        return None

    return stripped


def clean_script(text: str) -> str:
    """Devuelve solo el texto narrable: sin encabezados, acotaciones ni markdown."""
    if not text:
        return text

    out_lines = []
    for line in text.splitlines():
        processed = _process_line(line)
        if processed is None:
            continue
        out_lines.append(processed)

    # Reunir y normalizar saltos múltiples
    result = "\n".join(out_lines)
    result = re.sub(r"\n{3,}", "\n\n", result).strip()
    return result or text.strip()  # fallback: si quedó vacío, devolver original
