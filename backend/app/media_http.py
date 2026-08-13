"""Servir archivos a un <video> o un <audio> del navegador.

`FileResponse` no sirve para esto en la versión de Starlette que instala el
contenedor (0.37.2, la que fija fastapi==0.111.0): ignora la cabecera `Range`,
responde 200 con el archivo entero y ni siquiera anuncia `Accept-Ranges`.

Safari —el de macOS y el de iPhone— no reproduce un medio servido así. Antes de
empezar pide `Range: bytes=0-1` para saber si puede buscar dentro del archivo;
si le contestan 200 con todo el cuerpo, abandona. Y lo hace sin dar error: el
reproductor se queda en negro, como si el video no se hubiera generado nunca.
Chrome y Firefox son más tolerantes y lo reproducen igual, pero no dejan
adelantar ni mover la barra, y se tragan el archivo entero de una.

`media_library` ya había tropezado con esto y tenía su propia copia de esta
función para el reproductor de recorte; ahora vive acá y la usan todos.
"""

import mimetypes
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

# 64 KB: suficiente para que el reproductor arranque rápido sin cargar el
# archivo entero en memoria.
CHUNK = 64 * 1024

_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")

# Lo que el navegador pidió, cuando lo pedido no existe dentro del archivo.
_FUERA_DE_RANGO = object()


def _pedido(cabecera: str | None, size: int):
    """Interpreta la cabecera `Range`.

    Devuelve `(inicio, fin)` inclusive, `None` si no hay rango que atender (y
    entonces se manda el archivo entero), o `_FUERA_DE_RANGO` si pide algo que
    no está en el archivo.

    Sólo se atiende un rango: un `Range` con varios trozos es legal pero
    ningún reproductor lo usa, y contestar el archivo entero es una respuesta
    válida a un rango que no se quiere atender.
    """
    m = _RANGE.match((cabecera or "").strip())
    if not m or size == 0:
        return None

    ini, fin = m.group(1), m.group(2)
    if not ini and not fin:
        return None

    if not ini:
        # "bytes=-500" son los ÚLTIMOS 500 bytes, no los primeros. Salió mal en
        # la copia anterior de esta función.
        ultimos = int(fin)
        if ultimos == 0:
            return _FUERA_DE_RANGO
        return max(0, size - ultimos), size - 1

    inicio = int(ini)
    if inicio >= size:
        return _FUERA_DE_RANGO
    return inicio, min(int(fin), size - 1) if fin else size - 1


def _adjunto(nombre: str) -> str:
    """Content-Disposition para descargar, con el nombre intacto.

    Se mandan las dos formas: `filename` con lo que sea ASCII para los clientes
    viejos y `filename*` para que no se pierdan acentos ni eñes.
    """
    ascii_ = nombre.encode("ascii", "replace").decode("ascii").replace('"', "")
    return f"attachment; filename=\"{ascii_}\"; filename*=UTF-8''{quote(nombre)}"


def serve_media(path: Path, request: Request, *,
                download_name: str | None = None,
                media_type: str | None = None) -> StreamingResponse:
    """Sirve `path` con soporte de Range.

    Por defecto va `inline`, que es lo que necesita un `<video src=...>`. Con
    `download_name` va como descarga: eso es para el botón de descargar, no
    para el reproductor.
    """
    if not path.exists():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")

    size = path.stat().st_size
    ctype = media_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": _adjunto(download_name) if download_name else "inline",
    }

    pedido = _pedido(request.headers.get("range"), size)

    if pedido is _FUERA_DE_RANGO:
        raise HTTPException(status_code=416, detail="Rango fuera del archivo",
                            headers={"Content-Range": f"bytes */{size}",
                                     "Accept-Ranges": "bytes"})

    if pedido is None:
        inicio, fin, status = 0, size - 1, 200
    else:
        inicio, fin = pedido
        status = 206
        headers["Content-Range"] = f"bytes {inicio}-{fin}/{size}"

    largo = max(0, fin - inicio + 1)
    headers["Content-Length"] = str(largo)

    def _cuerpo():
        with path.open("rb") as f:
            f.seek(inicio)
            falta = largo
            while falta > 0:
                trozo = f.read(min(CHUNK, falta))
                if not trozo:
                    break
                falta -= len(trozo)
                yield trozo

    return StreamingResponse(_cuerpo(), status_code=status,
                             media_type=ctype, headers=headers)
