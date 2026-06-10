from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import projects, layers, render, publish

app = FastAPI(title="Mundial Video Bot API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router, prefix="/api/projects", tags=["projects"])
app.include_router(layers.router, prefix="/api/layers", tags=["layers"])
app.include_router(render.router, prefix="/api/render", tags=["render"])
app.include_router(publish.router, prefix="/api/publish", tags=["publish"])

@app.get("/")
def root():
    return {"status": "ok", "service": "Mundial Video Bot"}
