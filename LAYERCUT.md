# LayerCut — Guía de arquitectura

> Producción de video vertical para redes: de un tema a un MP4 con narración,
> imágenes, música y subtítulos.
>
> **Producción:** https://layercut-production.up.railway.app
> **Repo:** `ohg68/mundial-video-bot` · **Rama de trabajo:** `phase1/mobile-first-ui`
>
> ⚠️ **Railway despliega desde `main`.** Para que un cambio llegue a producción
> tiene que estar en `main`, no sólo en la rama de trabajo:
> `git push origin phase1/mobile-first-ui:main`.
> Un `railway up` manual es un override temporal: cualquier redeploy posterior
> —incluido el que dispara *cambiar una variable de entorno*— reconstruye desde
> `main` y lo pisa.

---

## Cómo se usa

Dos vías, sobre el mismo backend:

- **Bot de Telegram** — la principal. Acepta comandos escritos y **notas de voz**.
- **Web** — editor por capas, para ajustar lo que el bot generó.

---

## Stack

| Pieza | Tecnología |
|---|---|
| Backend | FastAPI (Python 3.11) + SQLAlchemy + SQLite |
| Frontend | React 18 + Vite + Tailwind CSS 4 |
| Video | FFmpeg (`filter_complex`) |
| Narración | edge-tts (por defecto), OpenAI TTS, ElevenLabs, Google TTS |
| Guiones | DeepSeek (por defecto), Claude, GPT-4o-mini |
| Voz a texto | faster-whisper **local** |
| Persistencia | Cloudinary (el disco de Railway es efímero) |
| Deploy | Docker en Railway |

**Por qué Whisper local y no API:** Groq Whisper está bloqueado geográficamente
en la región del usuario. Modelo `base` en CPU (int8), configurable con
`WHISPER_MODEL` — bajar a `tiny` si hay OOM. Corre en `asyncio.to_thread` porque
el bot y FastAPI comparten el event loop.

---

## Entrar (sin contraseña)

No hay contraseñas: nada que recordar ni que recuperar.

```
Telegram: /entrar  →  código de 6 dígitos (5 min, un solo uso)
Web:      pegar el código  →  sesión de 30 días
```

El código lo emite el bot, que ya conoce tu identidad. Pedir otro invalida el
anterior; 5 fallos en 15 minutos cortan con un 429.

**Llave de emergencia:** `LAYERCUT_RECOVERY_TOKEN` vale como sesión por sí misma
y se pega en el mismo campo del código. Es la salida si el bot se cae — se lee
del panel de Railway.

`/entrar` **exige** `TELEGRAM_ALLOWED_CHATS` configurada, aunque el bot esté
abierto para lo demás: sin whitelist, cualquiera que encontrara el bot pediría un
código y entraría.

Un middleware exige sesión en todo `/api` salvo `/api/auth/telegram/verify`,
`/api/auth/status` y las vistas públicas de share (`view`/`video`/`thumb`), cuyo
sentido es compartir con gente sin cuenta.

En el frontend, `api.js` **intercepta `window.fetch`** y añade la sesión a toda
petición a `/api` del propio origen. Es deliberado: hay decenas de llamadas con
`fetch` suelto por los componentes y así ninguna se puede saltar la cabecera.

---

## Las 5 capas

| Capa | Archivo | Qué es |
|---|---|---|
| `video` | `video.mp4` | Clips de bancos, fotos con Ken Burns o Google Drive |
| `audio` | `narration.mp3` | Narración TTS del guion |
| `music` | `music.mp3` | Fondo con fundidos |
| `subtitles` | `subtitles.srt` | El texto de la narración, sincronizado |
| `overlay` | `overlay.png` | Logo, con el fondo quitado por rembg |

Viven en `projects/{id}/{capa}/`. El render final es una cadena de
`filter_complex`: subtítulos y overlay sobre el video, narración y música
mezcladas con `amix`. Cola asíncrona de 3 workers, semáforo de 2 renders
simultáneos (`MAX_CONCURRENT_RENDERS`), progreso por WebSocket.

---

## Del tema al video

1. **Guion** — DeepSeek escribe con un presupuesto de **palabras**, no de
   segundos: a los modelos pedirles segundos no les sirve. `target_seconds`
   (30/60/90) × 2,5 palabras/s × la velocidad del TTS. Si aun así se pasa más de
   un 25%, `fit_to_duration` quita párrafos del medio conservando el gancho y la
   llamada a la acción.
2. **Keywords visuales en inglés** — el tema crudo en español daba resultados
   absurdos (buscar "mercado" traía bazares árabes para un video sobre el mercado
   portugués). `generate_visual_keywords` produce 5 términos en inglés.
3. **A/B split** (activado por defecto) — segmenta el guion en escenas y baja dos
   visuales por escena, para que la imagen siga lo que se narra. Sin esto las
   tomas se eligen con keywords genéricas y se ordenan al azar.
4. **Cadencia** — trocea en tomas de ~4 s siguiendo el audio, con segmentos
   únicos y barajados. Cada toma se normaliza a **30 fps constantes**: los clips
   de los bancos vienen a fps distintos y concatenarlos sin normalizar rompía los
   timestamps.

> **Dos generadores de guion.** La web pasa por `llm_service` (con plantillas);
> el bot tiene el suyo en `layer_service.generate_script`. Si cambiás cómo se
> pide el guion, hay que tocar los dos.

### Fuentes de video

Con rama real en el pipeline: `photos`, `mixed_photos`, `pexels`, `pixabay`,
`coverr`, `stock` (los tres bancos juntos), `wikimedia` (sólo imágenes, filtradas
a dominio público/CC0 para no necesitar atribución), `gdrive`, `local`.

`gdrive` y `wikimedia` son las únicas que **no** pasan por el camino A/B.

**YouTube está descartado** por copyright/ToS. El camino limpio: bajar con el
Downloader local o desde YouTube Studio, y subir a Drive.

---

## Persistencia y retención

El disco de Railway se borra en cada deploy. Cloudinary es la red de seguridad:
copia de la base cada 5 minutos y al apagar, restauración al arrancar.

Orden en el arranque (importa): **restore → purgado por retención →
reconciliación**.

- **Retención** (`RETENTION_DAYS`, 5): los archivos de un proyecto sin tocar
  caducan. La ficha (título, guion, config) se conserva y las capas vuelven a
  `empty`, así que se puede regenerar.
- **Reconciliación**: lo que la base dice `ready` pero no tiene archivo vuelve a
  `empty`, distinguiendo "caducó a propósito" de "el disco efímero se lo comió".

> ⚠️ **La trampa de `updated_at`.** Es el reloj de la retención y la columna
> lleva `onupdate`, así que *cualquier* escritura la pone al día y el proyecto
> deja de estar vencido — con lo que el restore le devuelve los archivos y la
> retención no caduca nunca. Por eso el mantenimiento usa
> `_conservar_updated_at()`, que necesita `flag_modified` (reasignar el mismo
> valor no ensucia la columna y SQLAlchemy la deja fuera del UPDATE). El restore
> además salta los proyectos vencidos. **No simplificar esto sin leer el porqué.**

> **Nunca escribir estado en `project.json`.** Es efímero y sólo se lee al migrar.
> Todo va por `project_service`, que escribe en SQLite. Este bug hizo que el guion
> y los cambios de config se perdieran en silencio.

---

## Variables de entorno

| Variable | ¿Hace falta? | Para qué |
|---|---|---|
| `JWT_SECRET` | **Sí** | Firma las sesiones. Sin ella se usa un valor por defecto que está en el repo. |
| `TELEGRAM_BOT_TOKEN` | **Sí** | El bot. Sin él no arranca y no hay forma de pedir código. |
| `TELEGRAM_ALLOWED_CHATS` | **Sí** | Chats autorizados. Sin ella `/entrar` se niega a emitir códigos. |
| `LAYERCUT_RECOVERY_TOKEN` | Recomendada | Llave de emergencia. Sin ella, un bot caído te deja fuera. |
| `DEEPSEEK_API_KEY` | **Sí** | Guiones, plan de escenas, keywords, asistente. |
| `CLOUDINARY_URL` | **Sí** en Railway | Backup y restore. Sin ella se pierde todo en cada deploy. |
| `PEXELS_API_KEY` | Recomendada | Fotos y clips. |
| `PIXABAY_API_KEY` | No | Otro banco. |
| `COVERR_API_KEY` | No | Otro banco. |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | No | Fuente Google Drive (cuenta de servicio). |
| `GDRIVE_VIDEO_FOLDER_ID` | No | Carpeta por defecto de Drive. |
| `GDRIVE_STORAGE_FOLDER_ID` | No | Almacenamiento en Drive. |
| `GOOGLE_CREDENTIALS_JSON` | No | Google TTS (`tts_provider: "google"`). |
| `ANTHROPIC_API_KEY` | No | Claude como LLM alternativo. |
| `OPENAI_API_KEY` | No | GPT-4o-mini y TTS HD. |
| `ELEVENLABS_API_KEY` | No | Voces ElevenLabs. |
| `HYPERFRAMES_URL` | No | Intros/outros animados. **Ver abajo.** |
| `RETENTION_DAYS` | No | Días antes de caducar (5). |
| `WHISPER_MODEL` | No | Modelo de transcripción (`base`). |
| `MAX_CONCURRENT_RENDERS` | No | Renders simultáneos (2). |
| `YOUTUBE_*`, `TIKTOK_*`, `INSTAGRAM_*` | No | Publicación. |

---

## Estado de las piezas

**HyperFrames** (intros/outros animados y captions kinéticos) era un servicio
Node aparte en Railway. **Ya no existe**, y su código no está en este repo.
`/api/motion/status` devuelve `configured` (la variable está puesta) y
`available` (el servicio responde) por separado — la interfaz mira `available`,
porque antes miraba sólo la variable y ofrecía funciones que fallaban al usarse.
Para recuperarlo hay que volver a levantar el microservicio; si no, quitar
`HYPERFRAMES_URL`.

---

## Estructura

```
backend/
  main.py                    # app, lifespan, middleware de sesión, SPA catch-all
  app/
    auth.py                  # login por código de Telegram + llave de emergencia
    database.py              # modelos SQLAlchemy
    task_queue.py            # cola async + progreso por WebSocket
    telegram_bot.py          # el bot entero (comandos, voz, teclados)
    models/project.py        # ProjectConfig y enums
    api/                     # projects, layers, render, publish, sources, share,
                             # gdrive, editing, motion, assistant, library
    services/
      project_service.py     # proyectos en SQLite + disco (retención incluida)
      layer_service.py       # ensamblado de la capa video, A/B, cadencia, voz
      render_service.py      # el render final con FFmpeg
      llm_service.py         # guiones con plantillas escalables
      script_utils.py        # limpieza de guion y presupuesto de duración
      cloud_storage.py       # backup/restore en Cloudinary
      ai_editor_service.py   # asistente conversacional (tool-calling)
      photo_sources.py       # fotos + Ken Burns
      video_sources.py       # búsqueda en bancos (el selector de clips)
      media_library_service.py, gdrive_service.py, motion_service.py,
      tts_service.py, voice_service.py, publish_service.py, editing_service.py
frontend/src/
  api.js                     # interceptor de fetch + sesión
  App.jsx                    # puerta de login + layout
  components/                # LayerCard, ScriptEditor, ProjectEditor, ...
tools/webapp/                # LayerCut Downloader: servidor LOCAL, no va a Railway
```

---

## Desarrollo local

```bash
# Backend
cd backend && python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
JWT_SECRET=dev DEEPSEEK_API_KEY=sk-xxx LAYERCUT_RECOVERY_TOKEN=dev-key \
  uvicorn main:app --reload --port 8000

# Frontend (proxy /api → :8000)
cd frontend && npm install && npm run dev
```

Sin bot en local, se entra con el valor de `LAYERCUT_RECOVERY_TOKEN`.

**El código usa sintaxis 3.10+** (`dict | None`): probar con `python3.11`.

---

## Deploy

```bash
git push origin phase1/mobile-first-ui:main   # esto es lo que despliega
railway status                                 # verificar que quede Online
```

Durante el arranque aparece un `409 Conflict: terminated by other getUpdates` en
los logs: el contenedor viejo sigue poleando mientras arranca el nuevo. Es normal
y se resuelve solo. **No** confundir con el bot muerto de verdad — ese síntoma es
`pending_update_count` que no baja en
`api.telegram.org/bot<token>/getWebhookInfo` sin que nadie polee.

**Tasks de asyncio:** guardar siempre la referencia (`app.state.*`). El loop sólo
mantiene weakrefs y el recolector puede llevarse una task a mitad de ejecución —
por eso el bot dejaba de responder de forma intermitente.
