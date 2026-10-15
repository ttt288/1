from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.decision import router as decision_router
from app.api.evaluation import router as evaluation_router
from app.api.health import router as health_router
from app.api.market import router as market_router
from app.core.config import get_settings


app = FastAPI(
    title="Adaptive Personalized Consumer Decision Agent",
    version="0.1.0",
)

app.include_router(health_router, prefix="/api")
app.include_router(decision_router, prefix="/api")
app.include_router(evaluation_router, prefix="/api")
app.include_router(market_router, prefix="/api")

settings = get_settings()
frontend_dir = settings.project_root / "frontend"
assets_dir = frontend_dir / "assets"

if assets_dir.exists():
    app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")


@app.get("/", include_in_schema=False)
def read_index() -> FileResponse:
    return FileResponse(frontend_dir / "index.html")
