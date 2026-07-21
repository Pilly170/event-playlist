import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.dependencies import get_db
from app.models.audit_log import write_audit_log
from app.models.config import get_config, update_config
from app.security.csrf import get_or_create_csrf_token, verify_csrf_token
from app.security.session import require_onboarded_admin

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="app/templates")


@router.get("/config")
async def config_form(
    request: Request,
    _username: str = Depends(require_onboarded_admin),
    db: sqlite3.Connection = Depends(get_db),
) -> Response:
    config = get_config(db)
    return templates.TemplateResponse(
        request,
        "admin/config.html",
        {"config": config, "csrf_token": get_or_create_csrf_token(request)},
    )


@router.post("/config")
async def config_submit(
    request: Request,
    require_admin_approval: bool = Form(False),
    exclude_explicit: bool = Form(False),
    default_playlist_id: str = Form(""),
    insert_tracks_ahead: int = Form(...),
    playlist_repeat_enabled: bool = Form(False),
    poll_interval_seconds: int = Form(...),
    username: str = Depends(require_onboarded_admin),
    db: sqlite3.Connection = Depends(get_db),
    _csrf: None = Depends(verify_csrf_token),
) -> Response:
    updated = update_config(
        db,
        require_admin_approval=require_admin_approval,
        exclude_explicit=exclude_explicit,
        default_playlist_id=default_playlist_id or None,
        insert_tracks_ahead=insert_tracks_ahead,
        playlist_repeat_enabled=playlist_repeat_enabled,
        poll_interval_seconds=poll_interval_seconds,
    )
    write_audit_log(db, actor=username, action="config.update", detail=str(updated))
    return RedirectResponse("/admin/config", status_code=303)
