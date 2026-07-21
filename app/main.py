import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

import httpx2
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.db import get_connection, run_migrations
from app.models.admin_users import count_admin_users, create_admin_user
from app.models.audit_log import write_audit_log
from app.routers import admin_auth, admin_config, admin_spotify, healthz
from app.security.auth import hash_password

APP_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

logger = logging.getLogger(__name__)

ADMIN_SESSION_MAX_AGE_SECONDS = 8 * 60 * 60


def _seed_default_admin_if_needed(conn) -> None:
    if count_admin_users(conn) > 0:
        return
    seed_password = secrets.token_urlsafe(16)
    create_admin_user(
        conn, username="admin", password_hash=hash_password(seed_password)
    )
    write_audit_log(
        conn, actor="system", action="admin.seeded", detail="username=admin"
    )
    logger.warning(
        "Seeded default admin user 'admin' with a one-time password: %s "
        "(you will be required to change it on first login)",
        seed_password,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(settings.database_path)
    run_migrations(conn)
    _seed_default_admin_if_needed(conn)
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
        https_only=settings.session_cookie_secure,
        max_age=ADMIN_SESSION_MAX_AGE_SECONDS,
    )
    app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
    app.include_router(healthz.router)
    app.include_router(admin_auth.router)
    app.include_router(admin_config.router)
    app.include_router(admin_spotify.router)

    @app.get("/")
    def placeholder(request: Request):
        return templates.TemplateResponse(request, "placeholder.html")

    return app


app = create_app()
