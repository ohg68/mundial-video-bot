# LayerCut — Contexto para seguir retocando

> Documento práctico de continuación. Para la arquitectura general ver [LAYERCUT.md](LAYERCUT.md).
> Última actualización: 2026-06-24 · Branch: `phase1/mobile-first-ui`

---

## Qué es esto ahora

LayerCut empezó como generador de videos del Mundial 2026, pero **hoy se opera
principalmente por un bot de Telegram** y genera videos de **cualquier tema**.
Pipeline: guión (LLM) → audio (TTS) → video (fotos/clips) → subtítulos → render → entrega.

- **Producción:** https://layercut-production.up.railway.app
- **Bot:** `@layercut_mundial_bot` (Telegram)
- **Deploy:** Railway (Docker) — `railway up --detach`

---

## Cómo correr y desplegar

```bash
# Local
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
DEEPSEEK_API_KEY=sk-xxx JWT_SECRET=dev uvicorn main:app --reload --port 8000

# Deploy (desde la raíz)
railway up --detach
# Esperar a que termine:
until railway status 2>&1 | grep -q "Online" && ! railway status 2>&1 | grep -qE "Building|Deploying|Initializing"; do sleep 10; done
```

El bot arranca dentro del mismo proceso de FastAPI (ver `lifespan` en `backend/main.py`),
activado por `TELEGRAM_BOT_TOKEN`. No hay proceso aparte.

---

## Variables de entorno (Railway)

| Variable | Estado | Uso |
|----------|--------|-----|
| `DEEPSEEK_API_KEY` | ✅ activa | Guiones + interpretación de comandos de voz |
| `PEXELS_API_KEY` | ✅ activa | Fotos y clips de video |
| `TELEGRAM_BOT_TOKEN` | ✅ activa | Bot de Telegram |
| `JWT_SECRET` | ✅ activa | Auth de la web |
| `TELEGRAM_ALLOWED_CHATS` | ⬜ no seteada | Whitelist del bot (vacía = **bot abierto**). Coma-separada |
| `WHISPER_MODEL` | ⬜ no seteada | Tamaño Whisper voz (default `base`; usar `tiny` si hay OOM) |
| `MAX_CONCURRENT_RENDERS` | ⬜ no seteada | Límite de renders FFmpeg simultáneos (default 2) |
| `PIXABAY_API_KEY` | ⬜ opcional | Fotos adicionales |
| `SERPAPI_API_KEY` | 🗑️ obsoleta | **Ya no se usa** — se puede borrar de Railway |

---

## Archivos clave (lo que tocamos en esta sesión)

```
backend/app/
├── telegram_bot.py          # TODO el bot: comandos, botones, voz, edición de capas
├── services/
│   ├── voice_service.py     # Whisper local (faster-whisper) + intención (DeepSeek)
│   ├── script_utils.py      # clean_script(): quita encabezados/acotaciones del LLM
│   ├── layer_service.py     # generate_script/audio/subtitles + assemble_video_layer
│   ├── render_service.py    # render FFmpeg + SRT→ASS (subtítulos) + semáforo
│   └── llm_service.py       # guiones para la web (templates)
└── models/project.py        # SubtitleLayerConfig.font_size=72 (px reales)
```

---

## Decisiones y gotchas (NO repetir errores ya resueltos)

1. **Persistencia: SIEMPRE SQLite, nunca `project.json`.**
   En Railway el filesystem es efímero; `project.json` solo se lee una vez al
   migrar al arrancar. Para guardar guión/config usar
   `project_service.update_project_config(pid, {...})`. (Bug raíz ya corregido.)

2. **Subtítulos: se queman como ASS con resolución real.**
   libass escala `FontSize` contra un canvas de 288px → con SRT crudo el texto
   salía gigante. Solución en `render_service._srt_to_ass()`: genera ASS con
   `PlayResX/Y` = resolución del video, así `font_size` es en píxeles reales.
   Control desde el bot: capa Subtitles → 📐 Ajustar tamaño/posición.

3. **Transcripción de voz: Whisper LOCAL, no Groq ni OpenAI.**
   Groq está bloqueado geográficamente para el usuario; no hay OPENAI_API_KEY.
   `voice_service` usa `faster-whisper` (CPU, int8). El modelo se descarga la
   1ª vez (~140MB). Si hay OOM en Railway, setear `WHISPER_MODEL=tiny`.
   La transcripción corre en `asyncio.to_thread` (bot + FastAPI comparten loop).

4. **Guión: limpiar SIEMPRE con `clean_script()`.**
   El LLM mete encabezados ("TEXTO DEL NARRADOR", "## Gancho (5s)", "(Tono...)").
   Ya se aplica en los generadores y antes de TTS/subtítulos. Si agregás otra
   ruta que produzca guión, pasalo por `clean_script()`.

5. **Prompt de guión es genérico.** No asume Mundial/fútbol salvo que el tema lo
   diga. Está en `layer_service.generate_script` (bot) y `llm_service._build_prompt` (web).

6. **Docker cache de Railway:** si un cambio en `backend/` no aparece en prod,
   forzá rebuild con un commit mínimo (la capa `COPY backend/ .` se cachea).

7. **Concurrencia:** el render usa un semáforo global (`MAX_CONCURRENT_RENDERS`)
   para no reventar la RAM con varios FFmpeg + Whisper a la vez.

---

## El bot de Telegram (mapa rápido)

**Flujo principal:** `/nuevo` → título → tema → [botón de fuente] → pipeline → video.
También por **nota de voz** suelta: transcribe → interpreta → crea el video.

**Comandos:** `/start` `/nuevo` `/ultimos` (=`/listar`) `/estado` `/descargar` `/capas` `/guion`.

**Todo por botones** (ya no hace falta copiar IDs):
- Al terminar un video → [📝 Guión] [🎬 Capas] [📥 Descargar]
- `/ultimos` → cada proyecto es un botón → menú de acciones (`pmenu_`)
- `_owns()` aísla por usuario solo si hay whitelist activa

**Callbacks (prefijos de `callback_data`):**
`pmenu_|pg_|pc_|pd_` (acciones de proyecto) · `layer_` (capa) · `dl_` (descargar capa)
· `edit_audio_|voice_menu_|setvoice_|regen_audio_|regen_video_|regen_subs_`
· `edit_script_|regen_script_` · `substyle_|subsz_|subps_|subrender_` (estilo subtítulos)
· `src_` (fuente en /nuevo)

---

## Pendientes / ideas para retocar

- **Limpiar `SERPAPI_API_KEY`** de Railway (obsoleta).
- **Subir archivos de música/overlay** desde el bot (hoy el botón "📤 Subir
  archivo" en capas music/overlay es placeholder, falta el handler de upload).
- **Persistencia de videos:** se pierden en cada deploy (storage efímero).
  Considerar volumen de Railway o S3 si se quiere historial real.
- **Seguridad del bot:** está abierto. Para cerrarlo, setear `TELEGRAM_ALLOWED_CHATS`
  (el chat_id se ve en `/start`).
- **Música de fondo:** el pipeline del bot no añade música; la capa existe pero
  no se genera automáticamente.
- **Posición "center" de subtítulos** quizá tape el sujeto; probar antes de usar.

---

## Cómo verificar cambios en producción (patrón usado)

Para subtítulos/video, renderizar vía API y extraer un frame (decodificar NO
necesita libass, así que sirve el ffmpeg local):

```bash
# tras crear proyecto + audio + subs + video + render:
curl -s "$PROD/api/render/$PID/download" -H "$H" -o /tmp/r.mp4
ffmpeg -y -ss 2 -i /tmp/r.mp4 -frames:v 1 /tmp/frame.png   # luego abrir el PNG
```

Para guiones, generar y revisar el texto directamente desde la respuesta JSON.
```
