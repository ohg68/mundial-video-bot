import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from app.api import projects, layers, render, publish, sources, share, gdrive, editing, motion, assistant, library
from app.auth import router as auth_router
from app.database import init_db
from app.migrate import migrate_json_to_db
from app.task_queue import task_queue
from app.websocket import websocket_endpoint

logging.basicConfig(level=logging.INFO)

# httpx loguea la URL completa de cada request en INFO. El polling del bot llama
# a api.telegram.org/bot<TOKEN>/getUpdates varias veces por segundo, así que el
# token acababa escrito miles de veces en los logs de Railway, en texto plano y
# al alcance de cualquiera con acceso al panel. En WARNING los errores de red
# siguen apareciendo.
logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    from app.services import cloud_storage

    await cloud_storage.restore_db()

    init_db()
    migrate_json_to_db()

    # Restaurar archivos de capas de todos los proyectos (disco efímero en Railway)
    try:
        await cloud_storage.restore_all_layers()
    except Exception as e:
        logging.getLogger(__name__).error(f"Restore de capas falló: {e}")

    # Retención: los proyectos que llevan más de RETENTION_DAYS sin tocarse
    # pierden sus archivos. Va después del restore para barrer también lo que
    # hubiera quedado de arranques anteriores.
    from app.services import project_service
    try:
        project_service.purge_expired_files()
    except Exception as e:
        logging.getLogger(__name__).error(f"Purgado por retención falló: {e}")

    # Después del restore: lo que la BD diga 'ready' pero no tenga archivo en
    # disco vuelve a 'empty'. Evita el estado mentiroso (UI ofrece renderizar
    # una capa que ya no existe y el render falla a mitad).
    try:
        project_service.reconcile_layer_states()
    except Exception as e:
        logging.getLogger(__name__).error(f"Reconciliación de capas falló: {e}")

    await task_queue.start()

    async def _periodic_backup():
        while True:
            await asyncio.sleep(300)
            try:
                await cloud_storage.backup_db()
            except Exception as e:
                logging.getLogger(__name__).error(f"Backup periódico falló: {e}")

    app.state.backup_task = asyncio.create_task(_periodic_backup())

    async def _periodic_purge():
        # Cada 6 h, para que un contenedor que lleve días arriba también respete
        # la retención sin esperar a un redeploy.
        while True:
            await asyncio.sleep(6 * 3600)
            try:
                r = await asyncio.to_thread(project_service.purge_expired_files)
                if r["purged"]:
                    logging.getLogger(__name__).info(
                        "Retención: %s proyectos purgados, %s MB liberados",
                        len(r["purged"]), r["freed_mb"])
            except Exception as e:
                logging.getLogger(__name__).error(f"Purgado periódico falló: {e}")

    # Guardar la referencia: el loop solo mantiene weakrefs y el GC puede matar
    # una task sin dueño (ya pasó con el polling del bot).
    app.state.purge_task = asyncio.create_task(_periodic_purge())

    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if telegram_token:
        from app.telegram_bot import start_polling
        # Guardar la referencia: el loop solo mantiene weakrefs a las tasks, y una
        # task sin referencia externa puede ser recolectada por el GC a mitad de
        # ejecución (por eso el bot dejaba de poletear de forma intermitente).
        app.state.bot_task = asyncio.create_task(start_polling(telegram_token))

        def _bot_done(task):
            try:
                exc = task.exception()
            except asyncio.CancelledError:
                exc = None
            if exc:
                logging.getLogger(__name__).error(
                    "La task del bot de Telegram terminó con error: %r", exc, exc_info=exc)

        app.state.bot_task.add_done_callback(_bot_done)
        logging.getLogger(__name__).info("Telegram bot iniciado")
    else:
        logging.getLogger(__name__).info("TELEGRAM_BOT_TOKEN no configurado — bot desactivado")

    yield
    await cloud_storage.backup_db()
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
app.include_router(gdrive.router, prefix="/api/gdrive", tags=["gdrive"])
app.include_router(editing.router, prefix="/api/editing", tags=["editing"])
app.include_router(motion.router, prefix="/api/motion", tags=["motion"])
app.include_router(assistant.router, prefix="/api/assistant", tags=["assistant"])
app.include_router(library.router, prefix="/api/library", tags=["library"])

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
