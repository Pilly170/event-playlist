import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.dependencies import get_database_path, get_db
from app.models.admin_users import get_by_username, record_login, update_password
from app.models.audit_log import write_audit_log
from app.security.auth import hash_password, verify_password
from app.security.csrf import get_or_create_csrf_token, verify_csrf_token
from app.security.session import log_in, log_out, require_admin
from app.services.admin_seed import clear_initial_admin_password_file
from app.services.login_lockout import is_locked_out

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="app/templates")


@router.get("/login")
async def login_form(request: Request) -> Response:
    return templates.TemplateResponse(request, "admin/login.html")


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: sqlite3.Connection = Depends(get_db),
) -> Response:
    # Checked before verifying the password (and before any Argon2 hashing work) —
    # once locked out, further attempts are rejected outright, not just wrong ones,
    # since that's the whole point of a lockout (SPEC.md §6.3/§8).
    if is_locked_out(db, username):
        write_audit_log(db, actor=username, action="login.blocked")
        return templates.TemplateResponse(
            request,
            "admin/login.html",
            {"error": "Too many failed login attempts. Try again in a few minutes."},
            status_code=429,
        )

    user = get_by_username(db, username)
    if user is None or not verify_password(password, user.password_hash):
        write_audit_log(db, actor=username, action="login.failure")
        return templates.TemplateResponse(
            request,
            "admin/login.html",
            {"error": "Invalid username or password"},
            status_code=401,
        )

    log_in(request, username)
    write_audit_log(db, actor=username, action="login.success")

    if user.last_login_at is None:
        return RedirectResponse("/admin/change-password", status_code=303)

    record_login(db, username)
    return RedirectResponse("/admin/config", status_code=303)


@router.get("/change-password")
async def change_password_form(
    request: Request, _username: str = Depends(require_admin)
) -> Response:
    return templates.TemplateResponse(
        request,
        "admin/change_password.html",
        {"csrf_token": get_or_create_csrf_token(request)},
    )


@router.post("/change-password")
async def change_password_submit(
    request: Request,
    new_password: str = Form(...),
    username: str = Depends(require_admin),
    db: sqlite3.Connection = Depends(get_db),
    database_path: str = Depends(get_database_path),
    _csrf: None = Depends(verify_csrf_token),
) -> Response:
    update_password(db, username, hash_password(new_password))
    record_login(db, username)
    write_audit_log(db, actor=username, action="password.changed")
    clear_initial_admin_password_file(database_path)
    return RedirectResponse("/admin/config", status_code=303)


@router.post("/logout")
async def logout(
    request: Request,
    _username: str = Depends(require_admin),
    _csrf: None = Depends(verify_csrf_token),
) -> Response:
    log_out(request)
    return RedirectResponse("/admin/login", status_code=303)
