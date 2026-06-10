# Mundial Video Bot ⚽

Editor de vídeos por capas para el Mundial 2026. Genera guiones con Claude, narración con Edge TTS, monta vídeos de tus clips o Pexels, y publica automáticamente en YouTube.

## Estructura

```
mundial-video-bot/
├── backend/          FastAPI + Python
│   ├── main.py
│   ├── app/
│   │   ├── api/      Endpoints REST
│   │   ├── services/ Lógica de capas + render
│   │   └── models/   Schemas Pydantic
│   └── requirements.txt
├── frontend/         React + Vite
│   └── src/
│       └── components/
└── .env.example
```

## Instalación

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp ../.env.example .env
# Edita .env con tus API keys

uvicorn main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Abre http://localhost:3000

## Uso del sistema de capas

Cada proyecto tiene 5 capas independientes:

| Capa | Archivo | Auto-generado | Reemplazable |
|------|---------|---------------|--------------|
| Vídeo | `video.mp4` | Sí (Pexels/local) | Sí |
| Narración | `narration.mp3` | Sí (Edge TTS) | Sí |
| Música | `music.mp3` | No | Sí |
| Subtítulos | `subtitles.srt` | Sí | Sí |
| Overlay | `overlay.png` | No | Sí |

Para reemplazar una capa con tu propio archivo:
- Abre el proyecto
- Expande la capa que quieres reemplazar
- Clic en "Reemplazar con mi archivo"
- Sube tu archivo (mp4, mp3, srt, png)
- El render final usará tu versión

## API REST

```
POST   /api/projects/                    Crear proyecto
GET    /api/projects/{id}                Estado del proyecto
POST   /api/layers/{id}/generate/{layer} Generar capa automáticamente
POST   /api/layers/{id}/replace/{layer}  Reemplazar capa con archivo propio
PATCH  /api/layers/{id}/config/{layer}   Actualizar config de capa
GET    /api/layers/{id}/download/{layer} Descargar capa individual
POST   /api/render/{id}                  Render final (combina todas las capas)
GET    /api/render/{id}/download         Descargar vídeo final
POST   /api/publish/{id}/youtube         Publicar en YouTube
```

## Variables de entorno

| Variable | Descripción |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Para generación de guiones con Claude |
| `PEXELS_API_KEY` | Para buscar vídeos stock |
| `LOCAL_CLIPS_DIR` | Carpeta con tus propios clips de vídeo |
| `YOUTUBE_*` | Credenciales OAuth para publicar en YouTube |

## Fuentes de vídeo

- **Local**: Usa clips de `LOCAL_CLIPS_DIR`
- **Pexels**: Busca automáticamente en Pexels según el tema
- **Mixto**: Primero clips locales, Pexels como fallback

## Deploy en Railway

```bash
# Backend
railway new
railway add --service backend
railway variables set ANTHROPIC_API_KEY=... PEXELS_API_KEY=...
railway up

# Frontend: build estático
npm run build
# Servir con nginx o deploy en Vercel
```

## Autor

Osvaldo — github.com/ohg68
