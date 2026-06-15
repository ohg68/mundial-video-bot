# LayerCut — Guia de Arquitectura y Modificaciones

> Plataforma de produccion de video para el Mundial 2026.
> Deploy: https://layercut-production.up.railway.app
> Repo: ohg68/mundial-video-bot | Branch: phase1/mobile-first-ui

---

## Stack

| Capa | Tecnologia |
|------|-----------|
| Frontend | React 18 + Vite + Tailwind CSS 4 |
| Backend | FastAPI + SQLAlchemy + SQLite |
| Video | FFmpeg (filter_complex, subtitles, overlay) |
| TTS | Edge-TTS, OpenAI TTS HD, ElevenLabs |
| LLM | DeepSeek, Claude, OpenAI (gpt-4o-mini) |
| Deploy | Docker (multi-stage) en Railway |

---

## Estructura de archivos

```
mundial-video-bot/
├── Dockerfile                    # Multi-stage: Node 20 (build) + Python 3.11 + FFmpeg
├── railway.toml                  # Config Railway (healthcheck /health)
│
├── backend/
│   ├── main.py                   # FastAPI app, lifespan, rutas, SPA catch-all
│   ├── requirements.txt          # Dependencias Python
│   │
│   ├── app/
│   │   ├── auth.py               # JWT HMAC-SHA256 (register/login/me)
│   │   ├── database.py           # SQLAlchemy models: User, Project, TaskRecord, ShareLink, ScheduledPost
│   │   ├── migrate.py            # Migra project.json → SQLite al arrancar
│   │   ├── task_queue.py         # Cola async con 3 workers + WebSocket progress
│   │   ├── websocket.py          # ConnectionManager por proyecto + parse FFmpeg progress
│   │   │
│   │   ├── models/
│   │   │   └── project.py        # Pydantic models: ProjectConfig, LayerConfigs, enums
│   │   │
│   │   ├── api/
│   │   │   ├── projects.py       # CRUD proyectos + stats/bulk-delete/duplicate/tags/category
│   │   │   ├── layers.py         # Upload/generate capas (video, audio, subtitles, overlay)
│   │   │   ├── render.py         # Render full/quick + history + durations (ffprobe)
│   │   │   ├── publish.py        # Publicar YouTube/TikTok/Instagram + schedule + thumbnail
│   │   │   ├── share.py          # Share links con token + expiracion + preview HTML
│   │   │   └── sources.py        # Clips (Pexels/Pixabay/Coverr/YouTube), TTS, LLM scripts
│   │   │
│   │   └── services/
│   │       ├── project_service.py    # Logica de proyectos (SQLite + filesystem)
│   │       ├── render_service.py     # FFmpeg render (filter_complex chain)
│   │       ├── llm_service.py        # DeepSeek/Claude/OpenAI + templates + timestamps
│   │       ├── tts_service.py        # Edge-TTS/OpenAI/ElevenLabs
│   │       ├── video_sources.py      # Pexels/Pixabay/Coverr/YouTube search + download
│   │       ├── photo_sources.py      # Google Images via SerpAPI + descarga paralela + Ken Burns FFmpeg
│   │       ├── publish_service.py    # Multi-platform publish + thumbnails
│   │       └── layer_service.py      # Ensamblado capa video (local/pexels/photos/mixed_photos)
│
├── frontend/
│   ├── vite.config.js            # Tailwind plugin + proxy /api → :8000
│   ├── index.html                # PWA meta tags + service worker
│   │
│   ├── src/
│   │   ├── main.jsx              # Entry point
│   │   ├── App.jsx               # Auth gate + drawer sidebar + BottomNav + routing
│   │   ├── api.js                # fetch wrapper con JWT auto-inject + 401 logout
│   │   ├── index.css             # @import "tailwindcss" + .btn-outline, .btn-action, .input-field
│   │   │
│   │   ├── hooks/
│   │   │   ├── useAuth.js        # login/register/logout + /me check
│   │   │   └── useProjectSocket.js # WebSocket auto-reconnect + progress/status
│   │   │
│   │   └── components/
│   │       ├── LoginForm.jsx     # Login/register toggle
│   │       ├── ProjectList.jsx   # Lista con bulk select, duplicate, category chips, stats
│   │       ├── ProjectEditor.jsx # Editor principal: layers + render + preview + publish
│   │       ├── LayerCard.jsx     # Card por capa: upload, ClipPicker, TTS selector
│   │       ├── ClipPicker.jsx    # Modal busqueda clips multi-source
│   │       ├── ScriptEditor.jsx  # Editor guion: LLM selector, templates, timestamps
│   │       ├── VideoPreview.jsx  # Player HTML5 con tabs (output/video/audio/music)
│   │       ├── Timeline.jsx      # 5 tracks color-coded proporcionales
│   │       ├── RenderHistory.jsx # Modal historial renders con download/delete
│   │       ├── PublishPanel.jsx  # Modal publicar multi-plataforma + share links + schedule
│   │       ├── NewProjectModal.jsx # Bottom-sheet crear proyecto
│   │       └── BottomNav.jsx     # Nav inferior mobile (Proyectos/Editor/Preview)
│   │
│   └── public/
│       ├── manifest.json         # PWA manifest
│       ├── sw.js                 # Service worker network-first
│       ├── icon-192.png
│       └── icon-512.png
```

---

## Variables de entorno (Railway)

| Variable | Requerida | Uso |
|----------|-----------|-----|
| `JWT_SECRET` | Si | Firma tokens auth |
| `DEEPSEEK_API_KEY` | Si* | Generacion de guiones |
| `ANTHROPIC_API_KEY` | No | Claude como LLM alternativo |
| `OPENAI_API_KEY` | No | GPT-4o-mini + TTS HD |
| `ELEVENLABS_API_KEY` | No | Voces ElevenLabs |
| `PEXELS_API_KEY` | No | Busqueda clips Pexels |
| `PIXABAY_API_KEY` | No | Busqueda clips Pixabay |
| `SERPAPI_API_KEY` | No | Busqueda fotos Google Images (fuente `photos` / `mixed_photos`) — fallback a Pexels si no esta configurada |
| `TIKTOK_ACCESS_TOKEN` | No | Publicar en TikTok |
| `INSTAGRAM_ACCESS_TOKEN` | No | Publicar en Instagram |
| `INSTAGRAM_USER_ID` | No | Publicar en Instagram |
| `YOUTUBE_TOKEN` | No | Publicar en YouTube |
| `YOUTUBE_REFRESH_TOKEN` | No | Publicar en YouTube |
| `YOUTUBE_CLIENT_ID` | No | Publicar en YouTube |
| `YOUTUBE_CLIENT_SECRET` | No | Publicar en YouTube |

*Al menos un LLM key es necesaria para generar guiones.

---

## API Endpoints

### Auth (`/api/auth`)
| Metodo | Ruta | Descripcion |
|--------|------|-------------|
| POST | `/register` | Crear usuario |
| POST | `/login` | Login → JWT |
| GET | `/me` | Usuario actual |

### Projects (`/api/projects`)
| Metodo | Ruta | Descripcion |
|--------|------|-------------|
| GET | `/` | Listar (filtros: category, tag, owner) |
| POST | `/` | Crear proyecto |
| GET | `/{id}` | Detalle proyecto |
| DELETE | `/{id}` | Eliminar |
| POST | `/{id}/duplicate` | Duplicar |
| POST | `/bulk-delete` | Eliminar varios |
| GET | `/stats` | Estadisticas disco |
| PATCH | `/{id}/tags` | Actualizar tags |
| PATCH | `/{id}/category` | Actualizar categoria |
| GET | `/{id}/size` | Tamano en disco |
| DELETE | `/{id}/renders` | Limpiar renders |

### Layers (`/api/layers`)
| Metodo | Ruta | Descripcion |
|--------|------|-------------|
| POST | `/{id}/upload/{layer}` | Subir archivo de capa |
| GET | `/{id}/download/{layer}` | Descargar capa |
| POST | `/{id}/generate/audio` | Generar audio TTS |
| POST | `/{id}/generate/subtitles` | Generar SRT desde script |
| PATCH | `/{id}/script` | Guardar guion |
| PATCH | `/{id}/config/llm` | Guardar prefs LLM |

### Render (`/api/render`)
| Metodo | Ruta | Descripcion |
|--------|------|-------------|
| POST | `/{id}` | Render full (1080p) |
| POST | `/{id}/quick` | Preview 540p |
| GET | `/{id}/download` | Descargar render |
| GET | `/{id}/durations` | Duraciones ffprobe |
| GET | `/{id}/history` | Historial renders |
| GET/DELETE | `/{id}/history/{file}` | Descargar/eliminar version |

### Publish (`/api/publish`)
| Metodo | Ruta | Descripcion |
|--------|------|-------------|
| POST | `/{id}/youtube` | Publicar YouTube |
| POST | `/{id}/tiktok` | Publicar TikTok |
| POST | `/{id}/instagram` | Publicar Instagram |
| POST | `/{id}/multi` | Publicar multi-plataforma |
| POST | `/{id}/thumbnail` | Generar thumbnail |
| POST | `/{id}/schedule` | Programar publicacion |
| GET | `/{id}/schedule` | Listar programadas |
| DELETE | `/{id}/schedule/{post_id}` | Cancelar programada |

### Share (`/api/share`)
| Metodo | Ruta | Descripcion |
|--------|------|-------------|
| POST | `/{id}/create` | Crear link compartido (72h) |
| GET | `/{id}/links` | Listar links |
| DELETE | `/{id}/links/{link_id}` | Eliminar link |
| GET | `/view/{token}` | Preview publica HTML |
| GET | `/video/{token}` | Stream video compartido |
| GET | `/thumb/{token}` | Thumbnail compartido |

### Sources (`/api/sources`)
| Metodo | Ruta | Descripcion |
|--------|------|-------------|
| GET | `/clips/search` | Buscar clips (Pexels/Pixabay/Coverr/YouTube) |
| POST | `/clips/download` | Descargar clip |
| POST | `/{id}/clips/upload` | Upload multiple clips |
| POST | `/tts/preview` | Preview voz TTS (5s) |
| GET | `/tts/voices` | Listar voces (Edge/OpenAI/ElevenLabs) |
| GET | `/tts/voices/elevenlabs` | Voces ElevenLabs |
| POST | `/script/generate` | Generar guion con IA |
| POST | `/script/timestamps` | Calcular timestamps |
| GET | `/script/templates` | Listar plantillas |

---

## Las 5 Capas

| # | Capa | Key | Color | Archivos |
|---|------|-----|-------|----------|
| 1 | Video | `video` | #0C447C | .mp4 en `projects/{id}/video/` |
| 2 | Narracion | `audio` | #27500A | .mp3 generado por TTS |
| 3 | Musica | `music` | #633806 | .mp3 en `projects/{id}/music/` |
| 4 | Subtitulos | `subtitles` | #3C3489 | .srt generado desde script |
| 5 | Overlay | `overlay` | #712B13 | .png logo/branding |

### Flujo de render (FFmpeg)
```
[video] → subtitles filter → overlay filter → [vout]
[audio] → volume → [narr]  ─┐
[music] → volume+fade → [music] ─┤→ amix → [aout]
                                   │
Output: -map [vout] -map [aout] → final.mp4
```

---

## Como hacer cambios comunes

### Agregar una nueva plantilla de guion
Editar `backend/app/services/llm_service.py`:
1. Agregar entrada en `TEMPLATES` dict (linea ~9)
2. Agregar nombre legible en `get_templates()` (linea ~193)

### Agregar una nueva voz TTS
Editar `backend/app/api/sources.py` → funcion `list_all_voices()` (linea ~72)

### Cambiar parametros de render
Editar `backend/app/services/render_service.py`:
- CRF: linea 132 (22=full, 32=quick)
- Preset: linea 133 (fast/ultrafast)
- Audio bitrate: linea 135 (192k/96k)

### Agregar nueva plataforma de publicacion
1. Crear funcion `publish_xxx()` en `backend/app/services/publish_service.py`
2. Agregar al dict `PUBLISHERS` (linea ~120)
3. Agregar ruta en `backend/app/api/publish.py`
4. Agregar boton en `frontend/src/components/PublishPanel.jsx` → array `PLATFORMS`

### Agregar nuevo endpoint API
1. Crear/editar archivo en `backend/app/api/`
2. Registrar router en `backend/main.py` con `app.include_router()`

### Modificar estilos globales
Editar `frontend/src/index.css` — clases utilitarias: `.btn-outline`, `.btn-action`, `.input-field`

### Cambiar colores del tema
Color principal: `#0C447C` (azul oscuro). Buscar y reemplazar en componentes JSX.

---

## Desarrollo local

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
DEEPSEEK_API_KEY=sk-xxx JWT_SECRET=dev uvicorn main:app --reload --port 8000

# Frontend
cd frontend
npm install
npm run dev    # → http://localhost:3000 (proxy → :8000)
```

## Deploy a Railway

```bash
# Desde la raiz del proyecto
railway login
railway link --service layercut
railway variables set DEEPSEEK_API_KEY=sk-xxx
railway up --detach
railway status        # Verificar que este Online
railway logs          # Ver logs de app
```

---

## Base de datos (SQLite)

Archivo: `layercut.db` en el directorio de trabajo.

### Tablas
- **users**: id, username, password_hash, created_at
- **projects**: id(8 chars), title, topic, match, match_date, category, tags(JSON), config(JSON), layers(JSON), layer_info(JSON), output, owner_id, timestamps
- **tasks**: id(uuid), project_id, task_type, status, progress, result, error, timestamps
- **share_links**: id(16 chars), project_id, token(64 chars), expires_at, views, created_at
- **scheduled_posts**: id(auto), project_id, platform, scheduled_at, status, meta(JSON), result, created_at

### Nota sobre almacenamiento
Los archivos de capas y renders se almacenan en `projects/{id}/` en el filesystem, no en la DB. En Railway esto es almacenamiento efimero — se pierde en cada deploy. Para persistencia, considerar un volumen de Railway o almacenamiento externo (S3).
