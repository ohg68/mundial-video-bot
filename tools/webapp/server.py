#!/usr/bin/env python3
"""LayerCut Downloader — servidor local (puerto 5757).

Herramienta aparte de la app principal: baja videos de YouTube/otras
plataformas (vía yt-dlp) a una carpeta local, para usarlos como fuente
"Mis vídeos" en un proyecto de LayerCut. Corre 100% en la Mac del
usuario, no en Railway.
"""
import json
import mimetypes
import re
import subprocess
import threading
import time
import urllib.parse
import uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 5757
DOWNLOAD_DIR = Path.home() / "Downloads" / "LayerCut Downloader"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# job_id -> {"status": "pending"|"done"|"error", "url": str, "file": str|None, "error": str|None}
JOBS = {}


# Motivos que YouTube devuelve cuando el cliente por defecto no pudo
# resolver el video. Son genéricos: tapan la causa real (DRM, privado,
# edad), así que ante uno de estos vale la pena reintentar con otro
# cliente para conseguir un mensaje que sirva.
VAGUE_ERRORS = (
    "this video is not available",
    "video unavailable",
    "failed to extract",
)

# Clientes alternativos para el reintento, en orden de confianza. Ojo:
# `tv` llegó a reportar "DRM protected" para un video que el cliente por
# defecto baja sin problema, así que va último y sólo se le cree si los
# otros dos también fallaron.
RETRY_CLIENTS = ("web_safari", "mweb", "tv")

# Presupuesto de tiempo para TODA la descarga, reintentos incluidos. Antes cada
# intento tenía su propio tope de 30 min, así que con tres reintentos el peor
# caso eran dos horas mientras el mensaje seguía diciendo "más de 30 minutos".
DESCARGA_TIMEOUT = 1800
RECORTE_TIMEOUT = 900

# (fragmento en el stderr de yt-dlp, explicación en castellano)
FRIENDLY_ERRORS = (
    ("drm protected", "El video se entrega con DRM (película, alquiler o contenido de pago) y no se "
                      "puede descargar. Si recién instalaste deno, probá de nuevo: en algunos casos "
                      "el DRM lo reportaba sólo el cliente de respaldo por faltar un runtime de JS."),
    ("requested format is not available",
     "No hay un formato descargable para este video. Puede faltar el runtime de JavaScript (deno)."),
    ("private video", "El video es privado."),
    ("members-only", "El video es solo para miembros del canal."),
    ("join this channel", "El video es solo para miembros del canal."),
    ("sign in to confirm your age", "El video tiene restricción de edad y requiere iniciar sesión."),
    ("age-restricted", "El video tiene restricción de edad y requiere iniciar sesión."),
    ("sign in to confirm you're not a bot", "YouTube pide verificación antihumano desde esta IP. "
                                            "Probá de nuevo más tarde."),
    ("not available in your country", "El video está bloqueado en tu país."),
    ("removed by the uploader", "El autor borró el video."),
    ("account associated with this video has been terminated",
     "La cuenta que subió el video fue dada de baja."),
    ("this video is not available", "YouTube reporta el video como no disponible."),
    ("video unavailable", "YouTube reporta el video como no disponible."),
    ("unsupported url", "yt-dlp no soporta ese sitio."),
    ("is not a valid url", "La URL no es válida."),
    # Los errores de red vienen con el stack de la excepción repetido dos
    # veces; queda un párrafo enorme e inútil en pantalla.
    ("failed to resolve", "No se pudo resolver el dominio. Revisá que la URL esté bien escrita."),
    ("unable to download webpage", "No se pudo acceder a la página. Revisá la URL y tu conexión."),
    ("connection refused", "No se pudo conectar con el sitio."),
)


def _is_youtube(url: str) -> bool:
    # Comparar por dominio y no por substring: "youtube.com" in host daba
    # verdadero para cosas como notyoutube.com.otro-sitio.net.
    host = urllib.parse.urlparse(url).netloc.lower().split(":")[0]
    return any(host == d or host.endswith("." + d) for d in ("youtube.com", "youtu.be"))


def _extract_error(stderr: str) -> str:
    """Saca de stderr la causa real del fallo, y nada más.

    yt-dlp escribe WARNING y ERROR mezclados en el mismo stream. Antes se
    volcaban los últimos 500 caracteres crudos, así que el aviso de
    "falta un runtime de JavaScript" quedaba pegado al error verdadero y
    el usuario veía un párrafo ilegible. Nos quedamos con la última línea
    ERROR y le sacamos los prefijos del extractor y del ID de video.
    """
    errores = [ln.strip() for ln in stderr.splitlines() if ln.strip().startswith("ERROR:")]
    if not errores:
        return ""
    msg = errores[-1][len("ERROR:"):].strip()
    msg = re.sub(r"^\[[^\]]+\]\s*", "", msg)      # "[youtube] "
    msg = re.sub(r"^[\w-]{11}:\s*", "", msg)      # "w8Y-WrJcZbo: " (los IDs son de 11 chars)
    # yt-dlp agrega instrucciones de bug report que acá no aportan nada.
    msg = re.split(r"\s*(?:Please report this issue|Confirm you are on the latest version)",
                   msg)[0]
    return msg.strip()


def _es_vago(msg: str) -> bool:
    bajo = msg.lower()
    return any(v in bajo for v in VAGUE_ERRORS)


def _explain(msg: str) -> str:
    bajo = msg.lower()
    for fragmento, explicacion in FRIENDLY_ERRORS:
        if fragmento in bajo:
            return explicacion
    return msg


def _yt_dlp(url: str, out_tmpl: str, client: str = "",
            timeout: float = DESCARGA_TIMEOUT) -> subprocess.CompletedProcess:
    """Corre yt-dlp pidiendo el mejor video + el mejor audio por separado.

    El selector viejo (`mp4/best`) sólo aceptaba archivos ya combinados,
    que YouTube publica en calidad baja: se bajaba todo en 360p/720p sin
    que se notara. Pidiendo las pistas por separado y uniéndolas con
    ffmpeg se consigue la resolución máxima real.
    """
    cmd = ["yt-dlp",
           "-f", "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b",
           "--merge-output-format", "mp4",
           "-o", out_tmpl, "--no-playlist"]
    if client:
        cmd += ["--extractor-args", f"youtube:player_client={client}"]
    cmd.append(url)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=max(1, timeout))


def _run_download(job_id: str, url: str):
    out_tmpl = str(DOWNLOAD_DIR / "%(title).80s.%(ext)s")

    def fallar(error: str):
        JOBS[job_id] = {"status": "error", "url": url, "file": None, "error": error}

    def listo():
        JOBS[job_id] = {"status": "done", "url": url, "file": None, "error": None}

    # El plazo es de la descarga entera, no de cada intento: lo que queda del
    # presupuesto es el tope del siguiente reintento.
    limite = time.monotonic() + DESCARGA_TIMEOUT
    restante = lambda: limite - time.monotonic()

    try:
        proc = _yt_dlp(url, out_tmpl, timeout=restante())
        if proc.returncode == 0:
            return listo()

        error = _extract_error(proc.stderr)
        # Cuando YouTube contesta algo genérico ("not available"), suele
        # ser el cliente y no el video: probando con otros clientes la
        # descarga arranca, y si igual falla al menos conseguimos un
        # motivo que se entienda.
        if _is_youtube(url) and (not error or _es_vago(error)):
            for cliente in RETRY_CLIENTS:
                if restante() <= 30:
                    break          # sin tiempo para otro intento con sentido
                reintento = _yt_dlp(url, out_tmpl, client=cliente, timeout=restante())
                if reintento.returncode == 0:
                    return listo()
                alterno = _extract_error(reintento.stderr)
                if alterno and not _es_vago(alterno):
                    error = alterno  # motivo concreto: no hace falta seguir probando
                    break
                error = alterno or error

        fallar(_explain(error) if error else "yt-dlp falló sin informar un motivo.")
    except FileNotFoundError:
        fallar("yt-dlp no está instalado. Corré: pip3 install yt-dlp")
    except subprocess.TimeoutExpired:
        fallar(f"La descarga superó los {DESCARGA_TIMEOUT // 60} minutos y se canceló.")
    except Exception as e:
        fallar(str(e)[:500])


def _probe_duration(path: Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=15,
        )
        return float(out.stdout.strip())
    except Exception:
        return 0.0


def _trim_clip(src: Path, dest: Path, start: float, end: float) -> tuple[bool, str]:
    """Recorta [start,end) de `src` a `dest`. Intenta stream-copy primero
    (rápido, sin recodificar); si el códec de origen no es compatible con el
    contenedor de salida, recodifica como fallback.

    Nunca lanza: devuelve (False, motivo). El timeout de ffmpeg se escapaba de
    aquí y del handler de /api/trim, así que recortar un video largo —el segundo
    paso recodifica y tarda— reventaba la petición con un 500 crudo en lugar del
    mensaje que el resto del archivo se toma el trabajo de construir.
    """
    duration = max(0.0, end - start)
    base = ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{duration:.3f}"]
    intentos = (
        base + ["-c", "copy", str(dest)],
        base + ["-c:v", "libx264", "-crf", "23", "-preset", "fast", "-c:a", "aac", str(dest)],
    )

    error = "ffmpeg falló al recortar"
    for cmd in intentos:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=RECORTE_TIMEOUT)
        except subprocess.TimeoutExpired:
            return False, (f"El recorte superó los {RECORTE_TIMEOUT // 60} minutos y se canceló. "
                           f"Probá con un tramo más corto.")
        except FileNotFoundError:
            return False, "ffmpeg no está instalado."
        if proc.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
            return True, ""
        error = proc.stderr[-500:] or error

    return False, error


INDEX_HTML = """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<title>LayerCut Downloader</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root { color-scheme: light; }
  body { font-family: -apple-system, sans-serif; max-width: 640px; margin: 40px auto; padding: 0 20px; color: #1a1a1a; background: #ffffff; }
  h1 { font-size: 20px; }
  input[type=text] { width: 100%; padding: 10px; border: 1px solid #ccc; border-radius: 8px; font-size: 14px; box-sizing: border-box; }
  button { margin-top: 10px; padding: 10px 18px; border: none; border-radius: 8px; background: #0C447C; color: white; font-size: 14px; cursor: pointer; }
  button:disabled { background: #aaa; }
  #status { margin-top: 16px; font-size: 13px; color: #555; }
  #files { margin-top: 24px; font-size: 13px; }
  #files .row { display: flex; align-items: center; gap: 8px; padding: 6px 0; border-bottom: 1px solid #eee; }
  #files .row span { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #files button { margin: 0; padding: 4px 10px; font-size: 12px; }
  #files button.danger { background: #c0392b; }
  #files button.secondary { background: #fff; color: #0C447C; border: 1px solid #ccc; }
  #player { margin-top: 16px; display: none; }
  #player video { width: 100%; border-radius: 8px; background: #000; }
  #editor { margin-top: 16px; display: none; border: 1px solid #eee; border-radius: 10px; padding: 14px; }
  #editor video { width: 100%; max-height: 280px; border-radius: 8px; background: #000; margin-bottom: 10px; }
  .slider-wrap { position: relative; height: 24px; margin-bottom: 8px; }
  /* Slider de doble handle: dos <input type=range> superpuestos ocupando
     el 100% del ancho. Sin esto, el que queda arriba en el DOM captura
     todos los clicks en toda la franja y el otro handle queda inalcanzable
     con el mouse (bug real encontrado y corregido en la app principal). */
  .dual-range { position: absolute; width: 100%; margin: 0; top: 4px; pointer-events: none; }
  .dual-range::-webkit-slider-thumb { pointer-events: auto; }
  .dual-range::-moz-range-thumb { pointer-events: auto; }
  #editorTimes { display: flex; justify-content: space-between; font-size: 12px; color: #555; margin-bottom: 8px; }
  #editorHint { font-size: 11px; color: #999; margin-bottom: 10px; }
  #editorError { font-size: 12px; color: #c0392b; margin-bottom: 8px; }
  .editor-actions { display: flex; gap: 8px; }
  .editor-actions button { flex: 1; margin: 0; }
  #editor .btn-secondary { background: #fff; color: #0C447C; border: 1px solid #ccc; }
</style></head>
<body>
  <h1>🎬 LayerCut Downloader</h1>
  <p style="color:#666;font-size:13px">Pegá un link de YouTube (u otro sitio soportado por yt-dlp) para bajarlo a tu carpeta de Descargas.</p>
  <input id="url" type="text" placeholder="https://www.youtube.com/watch?v=...">
  <button id="go" onclick="startDownload()">Descargar</button>
  <div id="status"></div>
  <div id="player"><video id="video" controls></video></div>
  <div id="editor">
    <video id="editorVideo" controls></video>
    <div class="slider-wrap">
      <input id="rangeStart" class="dual-range" type="range" min="0" max="0" step="0.1" value="0" oninput="onRangeChange()">
      <input id="rangeEnd" class="dual-range" type="range" min="0" max="0" step="0.1" value="0" oninput="onRangeChange()">
    </div>
    <div id="editorTimes">
      <span>IN: <b id="inTime">0:00.0</b></span>
      <span>Duración: <b id="selDuration">0:00.0</b></span>
      <span>OUT: <b id="outTime">0:00.0</b></span>
    </div>
    <div id="editorHint">El recorte puede variar ±1-2s por limitaciones de codificación (cae en el fotograma clave más cercano).</div>
    <div id="editorError"></div>
    <div class="editor-actions">
      <button class="btn-secondary" onclick="previewSelection()">▶ Previsualizar tramo</button>
      <button onclick="confirmTrim()">✂ Confirmar recorte</button>
    </div>
  </div>
  <div id="files"></div>
<script>
async function refreshFiles() {
  const r = await fetch('/api/files');
  const data = await r.json();
  const el = document.getElementById('files');
  el.innerHTML = data.files.length
    ? '<b>Descargados:</b>' + data.files.map(f => {
        const n = f.replace(/'/g, "\\'");
        return `<div class="row"><span>📄 ${f}</span>
          <button onclick="play('${n}')">▶ Ver</button>
          <button class="secondary" onclick="openEditor('${n}')">✂ Editar</button>
          <button class="secondary" onclick="reveal('${n}')">📁 Ubicación</button>
          <button class="danger" onclick="del('${n}')">🗑 Borrar</button></div>`;
      }).join('')
    : '';
}
function play(name) {
  const player = document.getElementById('player');
  const video = document.getElementById('video');
  video.src = '/files/' + encodeURIComponent(name);
  player.style.display = 'block';
  video.play().catch(() => {});
  player.scrollIntoView({behavior: 'smooth'});
}

let editorFile = null;
let editorPreviewing = false;

function formatTime(s) {
  if (!isFinite(s)) return '0:00.0';
  const m = Math.floor(s / 60);
  const sec = (s % 60).toFixed(1);
  return m + ':' + sec.padStart(4, '0');
}

async function openEditor(name) {
  editorFile = name;
  document.getElementById('player').style.display = 'none';
  document.getElementById('editorError').textContent = '';
  const r = await fetch('/api/duration/' + encodeURIComponent(name));
  const data = await r.json();
  const duration = data.duration || 0;
  const rs = document.getElementById('rangeStart');
  const re = document.getElementById('rangeEnd');
  rs.max = re.max = duration;
  rs.value = 0;
  re.value = duration;
  const ev = document.getElementById('editorVideo');
  ev.src = '/files/' + encodeURIComponent(name);
  ev.onended = () => { editorPreviewing = false; };
  ev.ontimeupdate = () => {
    if (editorPreviewing && ev.currentTime >= Number(re.value)) {
      ev.pause();
      editorPreviewing = false;
    }
  };
  onRangeChange();
  document.getElementById('editor').style.display = 'block';
  document.getElementById('editor').scrollIntoView({behavior: 'smooth'});
}

function onRangeChange() {
  const rs = document.getElementById('rangeStart');
  const re = document.getElementById('rangeEnd');
  let start = Number(rs.value);
  let end = Number(re.value);
  if (start > end - 0.1) { start = Math.max(0, end - 0.1); rs.value = start; }
  document.getElementById('inTime').textContent = formatTime(start);
  document.getElementById('outTime').textContent = formatTime(end);
  document.getElementById('selDuration').textContent = formatTime(Math.max(0, end - start));
}

function previewSelection() {
  const ev = document.getElementById('editorVideo');
  const start = Number(document.getElementById('rangeStart').value);
  ev.currentTime = start;
  ev.play().catch(() => {});
  editorPreviewing = true;
}

async function confirmTrim() {
  const start = Number(document.getElementById('rangeStart').value);
  const end = Number(document.getElementById('rangeEnd').value);
  const errEl = document.getElementById('editorError');
  errEl.textContent = '⏳ Recortando...';
  const r = await fetch('/api/trim', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: editorFile, start, end}),
  });
  const data = await r.json();
  if (!r.ok) {
    errEl.textContent = '❌ ' + (data.error || 'No se pudo recortar');
    return;
  }
  errEl.textContent = '';
  document.getElementById('editor').style.display = 'none';
  refreshFiles();
}
async function reveal(name) {
  await fetch('/api/reveal/' + encodeURIComponent(name), {method: 'POST'});
}
async function del(name) {
  if (!confirm('¿Borrar "' + name + '"? No se puede deshacer.')) return;
  const r = await fetch('/api/files/' + encodeURIComponent(name), {method: 'DELETE'});
  if (r.ok) {
    if (document.getElementById('video').src.includes(encodeURIComponent(name))) {
      document.getElementById('player').style.display = 'none';
    }
    refreshFiles();
  } else {
    alert('No se pudo borrar el archivo.');
  }
}
async function poll(jobId) {
  const r = await fetch('/api/status/' + jobId);
  const data = await r.json();
  const status = document.getElementById('status');
  if (data.status === 'pending') {
    status.textContent = '⏳ Descargando...';
    setTimeout(() => poll(jobId), 1500);
  } else if (data.status === 'done') {
    status.textContent = '✅ Listo — guardado en ~/Downloads/LayerCut Downloader';
    document.getElementById('go').disabled = false;
    refreshFiles();
  } else {
    status.textContent = '❌ ' + (data.error || 'Error');
    document.getElementById('go').disabled = false;
  }
}
async function startDownload() {
  const url = document.getElementById('url').value.trim();
  if (!url) return;
  document.getElementById('go').disabled = true;
  document.getElementById('status').textContent = '⏳ Iniciando...';
  const r = await fetch('/api/download', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({url}),
  });
  const data = await r.json();
  if (data.job_id) poll(data.job_id);
}
refreshFiles();
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # silencioso, no ensuciar la terminal

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/ping":
            return self._json({"ok": True})
        if self.path == "/api/files":
            files = sorted(p.name for p in DOWNLOAD_DIR.glob("*") if p.is_file())
            return self._json({"files": files})
        if self.path.startswith("/api/status/"):
            job_id = self.path.rsplit("/", 1)[-1]
            return self._json(JOBS.get(job_id, {"status": "error", "error": "job no encontrado"}))
        if self.path.startswith("/api/duration/"):
            path = self._resolve_safe(self.path[len("/api/duration/"):])
            if path is None:
                return self._json({"error": "Archivo no encontrado"}, 404)
            return self._json({"duration": _probe_duration(path)})
        if self.path.startswith("/files/"):
            return self._serve_file(self.path[len("/files/"):])
        if self.path == "/" or self.path == "/index.html":
            body = INDEX_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def _resolve_safe(self, raw_name: str):
        """Resuelve un nombre de archivo dentro de DOWNLOAD_DIR, o None si no
        existe o intenta escapar del directorio (path traversal)."""
        name = urllib.parse.unquote(raw_name)
        path = (DOWNLOAD_DIR / name).resolve()
        if DOWNLOAD_DIR.resolve() not in path.parents or not path.is_file():
            return None
        return path

    def _serve_file(self, raw_name: str):
        path = self._resolve_safe(raw_name)
        if path is None:
            self.send_response(404)
            self.end_headers()
            return

        size = path.stat().st_size
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        range_header = self.headers.get("Range")

        start, end = 0, size - 1
        status = 200
        if range_header and range_header.startswith("bytes="):
            status = 206
            rng = range_header[6:].split("-")
            start = int(rng[0]) if rng[0] else 0
            end = int(rng[1]) if len(rng) > 1 and rng[1] else size - 1
            end = min(end, size - 1)

        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()

        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                remaining -= len(chunk)

    def do_POST(self):
        if self.path == "/api/download":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
            url = (data.get("url") or "").strip()
            if not url:
                return self._json({"error": "Falta la URL"}, 400)
            job_id = uuid.uuid4().hex[:8]
            JOBS[job_id] = {"status": "pending", "url": url, "file": None, "error": None}
            threading.Thread(target=_run_download, args=(job_id, url), daemon=True).start()
            return self._json({"job_id": job_id})
        if self.path.startswith("/api/reveal/"):
            path = self._resolve_safe(self.path[len("/api/reveal/"):])
            if path is None:
                return self._json({"error": "Archivo no encontrado"}, 404)
            subprocess.run(["open", "-R", str(path)])
            return self._json({"ok": True})
        if self.path == "/api/trim":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
            src = self._resolve_safe(data.get("name") or "")
            if src is None:
                return self._json({"error": "Archivo no encontrado"}, 404)
            try:
                start = float(data.get("start"))
                end = float(data.get("end"))
            except (TypeError, ValueError):
                return self._json({"error": "start/end deben ser números"}, 400)
            if start < 0 or end <= start:
                return self._json({"error": "Rango de recorte inválido"}, 400)
            dest = src.with_name(f"{src.stem} (recorte {start:.1f}-{end:.1f}s){src.suffix}")
            ok, err = _trim_clip(src, dest, start, end)
            if not ok:
                return self._json({"error": err}, 500)
            return self._json({"ok": True, "file": dest.name})
        self.send_response(404)
        self.end_headers()

    def do_DELETE(self):
        if self.path.startswith("/api/files/"):
            path = self._resolve_safe(self.path[len("/api/files/"):])
            if path is None:
                return self._json({"error": "Archivo no encontrado"}, 404)
            path.unlink()
            return self._json({"ok": True})
        self.send_response(404)
        self.end_headers()


if __name__ == "__main__":
    print(f"LayerCut Downloader escuchando en http://localhost:{PORT}")
    print(f"Descargas en: {DOWNLOAD_DIR}")
    ThreadingHTTPServer(("localhost", PORT), Handler).serve_forever()
