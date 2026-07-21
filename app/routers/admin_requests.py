import sqlite3
from urllib.parse import quote

import httpx2
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.dependencies import get_cipher, get_db, get_http_client
from app.models.requests import list_added, list_pending
from app.security.session import require_onboarded_admin
from app.services.crypto import TokenCipher
from app.services.playlist_ops import (
    PlaylistApprovalError,
    approve_request,
    deny_request,
)

router = APIRouter(prefix="/admin/requests")
templates = Jinja2Templates(directory="app/templates")


@router.get("")
async def requests_queue(
    request: Request,
    error: str = "",
    _username: str = Depends(require_onboarded_admin),
    db: sqlite3.Connection = Depends(get_db),
) -> Response:
    return templates.TemplateResponse(
        request,
        "admin/requests.html",
        {"pending": list_pending(db), "added": list_added(db), "error": error},
    )


@router.post("/{request_id}/approve")
async def approve(
    request_id: int,
    username: str = Depends(require_onboarded_admin),
    db: sqlite3.Connection = Depends(get_db),
    cipher: TokenCipher = Depends(get_cipher),
    client: httpx2.AsyncClient = Depends(get_http_client),
) -> Response:
    try:
        await approve_request(
            db,
            cipher,
            client,
            client_id=settings.spotify_client_id,
            client_secret=settings.spotify_client_secret,
            request_id=request_id,
            admin_username=username,
        )
    except PlaylistApprovalError as exc:
        return RedirectResponse(
            f"/admin/requests?error={quote(str(exc))}", status_code=303
        )
    return RedirectResponse("/admin/requests", status_code=303)


@router.post("/{request_id}/deny")
async def deny(
    request_id: int,
    username: str = Depends(require_onboarded_admin),
    db: sqlite3.Connection = Depends(get_db),
) -> Response:
    try:
        deny_request(db, request_id=request_id, admin_username=username)
    except PlaylistApprovalError as exc:
        return RedirectResponse(
            f"/admin/requests?error={quote(str(exc))}", status_code=303
        )
    return RedirectResponse("/admin/requests", status_code=303)
