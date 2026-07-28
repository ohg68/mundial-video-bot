#!/usr/bin/env python3
"""LayerCut Downloader — servidor local (puerto 5757).

Herramienta aparte de la app principal: baja videos de YouTube/otras
plataformas (vía yt-dlp) a una carpeta local, para usarlos como fuente
"Mis vídeos" en un proyecto de LayerCut. Corre 100% en la Mac del
usuario, no en Railway.
"""
import json
import mimetypes
import subprocess
import threading
import urllib.parse
import uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 5757
DOWNLOAD_DIR = Path.home() / "Downloads" / "LayerCut Downloader"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# job_id -> {"status": "pending"|"done"|"error", "url": str, "file": str|None, "error": str|None}
JOBS = {}


def _run_download(job_id: str, url: str):
    out_tmpl = str(DOWNLOAD_DIR / "%(title).80s.%(ext)s")
    try:
        proc = subprocess.run(
            ["yt-dlp", "-f", "mp4/best", "-o", out_tmpl, "--no-playlist", url],
            capture_output=True, text=True, timeout=1800,
        )
        if proc.returncode != 0:
            JOBS[job_id] = {"status": "error", "url": url, "file": None,
                             "error": proc.stderr[-500:] or "yt-dlp falló"}
            return
        JOBS[job_id] = {"status": "done", "url": url, "file": None, "error": None}
    except FileNotFoundError:
        JOBS[job_id] = {"status": "error", "url": url, "file": None,
                         "error": "yt-dlp no está instalado. Corré: pip3 install yt-dlp"}
    except Exception as e:
        JOBS[job_id] = {"status": "error", "url": url, "file": None, "error": str(e)[:500]}


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
  #player { margin-top: 16px; display: none; }
  #player video { width: 100%; border-radius: 8px; background: #000; }
</style></head>
<body>
  <h1>🎬 LayerCut Downloader</h1>
  <p style="color:#666;font-size:13px">Pegá un link de YouTube (u otro sitio soportado por yt-dlp) para bajarlo a tu carpeta de Descargas.</p>
  <input id="url" type="text" placeholder="https://www.youtube.com/watch?v=...">
  <button id="go" onclick="startDownload()">Descargar</button>
  <div id="status"></div>
  <div id="player"><video id="video" controls></video></div>
  <div id="files"></div>
<script>
async function refreshFiles() {
  const r = await fetch('/api/files');
  const data = await r.json();
  const el = document.getElementById('files');
  el.innerHTML = data.files.length
    ? '<b>Descargados:</b>' + data.files.map(f =>
        `<div class="row"><span>📄 ${f}</span><button onclick="play('${f.replace(/'/g, "\\'")}')">▶ Ver</button></div>`
      ).join('')
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

    def _serve_file(self, raw_name: str):
        name = urllib.parse.unquote(raw_name)
        path = (DOWNLOAD_DIR / name).resolve()
        # Path traversal guard: el archivo resuelto debe seguir dentro de DOWNLOAD_DIR.
        if DOWNLOAD_DIR.resolve() not in path.parents or not path.is_file():
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
        self.send_response(404)
        self.end_headers()


if __name__ == "__main__":
    print(f"LayerCut Downloader escuchando en http://localhost:{PORT}")
    print(f"Descargas en: {DOWNLOAD_DIR}")
    ThreadingHTTPServer(("localhost", PORT), Handler).serve_forever()
