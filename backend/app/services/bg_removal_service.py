"""Quitado de fondo de imágenes (overlay/logo) vía rembg (modelo ISNet local).

Equivalente server-side de herramientas como rm-bg (cliente, navegador):
mismo tipo de modelo (ISNet), corrido acá en el backend para que el usuario
no dependa de una página externa al preparar su overlay/marca de agua.
"""
import asyncio
import logging
from pathlib import Path

log = logging.getLogger(__name__)

MODEL_NAME = "isnet-general-use"

_session = None


def _get_session():
    global _session
    if _session is None:
        from rembg import new_session
        _session = new_session(MODEL_NAME)
    return _session


def _remove_sync(input_path: Path, output_path: Path):
    from rembg import remove
    session = _get_session()
    data = input_path.read_bytes()
    result = remove(data, session=session)
    output_path.write_bytes(result)


async def remove_background(input_path: Path, output_path: Path) -> bool:
    """Quita el fondo de input_path y escribe un PNG con transparencia en
    output_path. Devuelve False si algo falla (input inexistente, error del
    modelo, etc.) para que el llamador decida cómo informarlo."""
    if not input_path.exists():
        log.warning(f"remove_background: no existe {input_path}")
        return False
    try:
        await asyncio.to_thread(_remove_sync, input_path, output_path)
        return output_path.exists() and output_path.stat().st_size > 0
    except Exception as e:
        log.error(f"remove_background falló: {e}")
        return False
