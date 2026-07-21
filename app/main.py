from contextlib import asynccontextmanager
from pathlib import Path

import httpx2
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.db import get_connection, run_migrations
from app.routers import (
    admin_auth,
    admin_config,
    admin_spotify,
    healthz,
    public_form,
    public_menu,
)
from app.services.admin_seed import seed_default_admin_if_needed

APP_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

ADMIN_SESSION_MAX_AGE_SECONDS = 8 * 60 * 60


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(settings.database_path)
    run_migrations(conn)
    seed_default_admin_if_needed(conn, database_path=settings.database_path)
    conn.close()

    app.state.http_client = httpx2.AsyncClient()
    yield
    await app.state.http_client.aclose()


def create_app() -> FastAPI:
    app = FastAPI(title="Event Playlist", lifespan=lifespan)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret_key,
        same_site="strict",
        https_only=settings.secure_cookies,
        max_age=ADMIN_SESSION_MAX_AGE_SECONDS,
    )
    app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
    app.include_router(healthz.router)
    app.include_router(admin_auth.router)
    app.include_router(admin_config.router)
    app.include_router(admin_spotify.router)
    app.include_router(public_form.router)
    app.include_router(public_menu.router)

    @app.get("/")
    def placeholder(request: Request):
        return templates.TemplateResponse(request, "placeholder.html")

    return app


app = create_app()
