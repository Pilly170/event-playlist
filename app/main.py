import asyncio
import contextlib
from contextlib import asynccontextmanager
from pathlib import Path

import httpx2
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.db import get_connection, run_migrations
from app.routers import (
    admin_auth,
    admin_config,
    admin_requests,
    admin_spotify,
    healthz,
    public_form,
    public_menu,
)
from app.services.admin_seed import seed_default_admin_if_needed
from app.services.crypto import TokenCipher
from app.worker.poller import poll_forever

APP_DIR = Path(__file__).parent

ADMIN_SESSION_MAX_AGE_SECONDS = 8 * 60 * 60


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(settings.database_path)
    run_migrations(conn)
    seed_default_admin_if_needed(conn, database_path=settings.database_path)
    conn.close()

    app.state.http_client = httpx2.AsyncClient()

    # Single in-process asyncio task, not a separate service — SPEC.md §2/§6.4
    # require the app to run as exactly one process (uvicorn --workers 1, one
    # container replica) specifically because of this poller. Running multiple
    # workers/replicas would start it multiple times, duplicating Spotify API
    # calls and racing playlist edits from independent pollers.
    poller_stop_event = asyncio.Event()
    poller_task = asyncio.create_task(
        poll_forever(
            settings.database_path,
            TokenCipher(key=settings.token_encryption_key),
            app.state.http_client,
            client_id=settings.spotify_client_id,
            client_secret=settings.spotify_client_secret,
            stop_event=poller_stop_event,
        )
    )

    yield

    poller_stop_event.set()
    with contextlib.suppress(asyncio.CancelledError):
        await poller_task
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
    app.include_router(admin_requests.router)
    app.include_router(admin_spotify.router)
    app.include_router(public_form.router)
    app.include_router(public_menu.router)

    @app.get("/")
    def root() -> RedirectResponse:
        return RedirectResponse("/request")

    return app


app = create_app()
