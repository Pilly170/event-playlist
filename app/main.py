from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.routers import healthz

APP_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))


def create_app() -> FastAPI:
    app = FastAPI(title="Event Playlist")
    app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
    app.include_router(healthz.router)

    @app.get("/")
    def placeholder(request: Request):
        return templates.TemplateResponse(request, "placeholder.html")

    return app


app = create_app()
