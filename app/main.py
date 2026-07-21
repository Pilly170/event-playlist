from contextlib import asynccontextmanager
from pathlib import Path

import httpx2
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.db import get_connection, run_migrations
from app.routers import admin_spotify, healthz

APP_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(settings.database_path)
    run_migrations(conn)
    conn.close()

    app.state.http_client = httpx2.AsyncClient()
    yield
    await app.state.http_client.aclose()


def create_app() -> FastAPI:
    app = FastAPI(title="Event Playlist", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
    app.include_router(healthz.router)
    app.include_router(admin_spotify.router)

    @app.get("/")
    def placeholder(request: Request):
        return templates.TemplateResponse(request, "placeholder.html")

    return app


app = create_app()
