import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from app.api import projects, layers, render, publish, sources, share
from app.auth import router as auth_router
from app.database import init_db
from app.migrate import migrate_json_to_db
from app.task_queue import task_queue
from app.websocket import websocket_endpoint

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    init_db()
    migrate_json_to_db()
    await task_queue.start()

    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if telegram_token:
        from app.telegram_bot import start_polling
        asyncio.create_task(start_polling(telegram_token))
        logging.getLogger(__name__).info("Telegram bot iniciado")
    else:
        logging.getLogger(__name__).info("TELEGRAM_BOT_TOKEN no configurado — bot desactivado")

    yield
    await task_queue.stop()


app = FastAPI(title="LayerCut API", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(projects.router, prefix="/api/projects", tags=["projects"])
app.include_router(layers.router, prefix="/api/layers", tags=["layers"])
app.include_router(render.router, prefix="/api/render", tags=["render"])
app.include_router(publish.router, prefix="/api/publish", tags=["publish"])
app.include_router(sources.router, prefix="/api/sources", tags=["sources"])
app.include_router(share.router, prefix="/api/share", tags=["share"])

app.add_api_websocket_route("/ws/{project_id}", websocket_endpoint)


@app.get("/health")
def health():
    return {"status": "ok", "service": "LayerCut"}


@app.get("/share/{token}")
async def share_redirect(token: str):
    from fastapi import Request
    from app.api.share import view_share
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        return await view_share(token, db)
    finally:
        db.close()


static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    assets_dir = os.path.join(static_dir, "assets")
    if os.path.exists(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/")
    async def serve_index():
        return FileResponse(os.path.join(static_dir, "index.html"))

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        if full_path.startswith(("api", "ws", "share")) or full_path == "health":
            from fastapi import HTTPException
            raise HTTPException(status_code=404)
        return FileResponse(os.path.join(static_dir, "index.html"))
