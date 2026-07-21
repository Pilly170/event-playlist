import sqlite3

import httpx2
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.dependencies import get_cipher, get_db, get_http_client
from app.models.config import get_config
from app.models.requests import get_by_reference_code
from app.services.crypto import TokenCipher
from app.spotify.auth_manager import SpotifyNotConnectedError
from app.spotify.client import get_now_playing, get_playlist_tracks

router = APIRouter(prefix="/menu")
templates = Jinja2Templates(directory="app/templates")

PLAYLIST_PAGE_SIZE = 20


@router.get("")
async def menu(
    request: Request,
    code: str = Query("", max_length=20),
    db: sqlite3.Connection = Depends(get_db),
    cipher: TokenCipher = Depends(get_cipher),
    client: httpx2.AsyncClient = Depends(get_http_client),
) -> Response:
    now_playing = None
    now_playing_error = None
    try:
        now_playing = await get_now_playing(
            db,
            cipher,
            client,
            client_id=settings.spotify_client_id,
            client_secret=settings.spotify_client_secret,
        )
    except SpotifyNotConnectedError:
        now_playing_error = "Now playing is temporarily unavailable."

    # Public status lookup is by reference code only, not name (SPEC.md §6.2) — a
    # denied request's row no longer exists (Phase 5), so a stale/invalid/denied
    # code and a never-issued one are indistinguishable here on purpose.
    status_result = None
    stripped_code = code.strip()
    if stripped_code:
        status_result = get_by_reference_code(db, stripped_code.upper())

    return templates.TemplateResponse(
        request,
        "public/menu.html",
        {
            "now_playing": now_playing,
            "now_playing_error": now_playing_error,
            "code": code,
            "searched": bool(stripped_code),
            "status_result": status_result,
        },
    )


@router.get("/playlist")
async def playlist(
    request: Request,
    offset: int = 0,
    db: sqlite3.Connection = Depends(get_db),
    cipher: TokenCipher = Depends(get_cipher),
    client: httpx2.AsyncClient = Depends(get_http_client),
) -> Response:
    config = get_config(db)
    if not config.default_playlist_id:
        return templates.TemplateResponse(
            request, "public/playlist.html", {"configured": False}
        )

    safe_offset = max(offset, 0)
    try:
        page = await get_playlist_tracks(
            db,
            cipher,
            client,
            client_id=settings.spotify_client_id,
            client_secret=settings.spotify_client_secret,
            playlist_id=config.default_playlist_id,
            limit=PLAYLIST_PAGE_SIZE,
            offset=safe_offset,
        )
    except SpotifyNotConnectedError:
        return templates.TemplateResponse(
            request,
            "public/playlist.html",
            {"configured": True, "error": "The playlist is temporarily unavailable."},
        )

    return templates.TemplateResponse(
        request,
        "public/playlist.html",
        {
            "configured": True,
            "page": page,
            "has_previous": page.offset > 0,
            "has_next": page.offset + len(page.items) < page.total,
            "previous_offset": max(page.offset - PLAYLIST_PAGE_SIZE, 0),
            "next_offset": page.offset + PLAYLIST_PAGE_SIZE,
        },
    )
