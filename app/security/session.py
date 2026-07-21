import sqlite3

from fastapi import Depends, HTTPException, Request

from app.dependencies import get_db
from app.models.admin_users import get_by_username

SESSION_USERNAME_KEY = "admin_username"


def log_in(request: Request, username: str) -> None:
    request.session.clear()
    request.session[SESSION_USERNAME_KEY] = username


def log_out(request: Request) -> None:
    request.session.clear()


async def require_admin(request: Request) -> str:
    username = request.session.get(SESSION_USERNAME_KEY)
    if username is None:
        raise HTTPException(status_code=303, headers={"location": "/admin/login"})
    return username


async def require_onboarded_admin(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
) -> str:
    # Onboarding (forced password change) is tracked via admin_users.last_login_at
    # being NULL, not a session flag — a session flag would stop enforcing this
    # the moment a half-onboarded user closed the tab and started a new session.
    username = await require_admin(request)
    user = get_by_username(db, username)
    if user is None or user.last_login_at is None:
        raise HTTPException(
            status_code=303, headers={"location": "/admin/change-password"}
        )
    return username
