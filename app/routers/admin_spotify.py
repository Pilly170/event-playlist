import secrets
import sqlite3

import httpx2
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.config import settings
from app.dependencies import get_cipher, get_db, get_http_client
from app.security.session import require_onboarded_admin
from app.services.crypto import TokenCipher
from app.spotify.oauth import build_authorize_url, exchange_code_for_token
from app.spotify.token_store import load_tokens, save_tokens

router = APIRouter(
    prefix="/admin/spotify", dependencies=[Depends(require_onboarded_admin)]
)

STATE_COOKIE_NAME = "spotify_oauth_state"


@router.get("/connect")
def connect() -> RedirectResponse:
    state = secrets.token_urlsafe(24)
    authorize_url = build_authorize_url(
        client_id=settings.spotify_client_id,
        redirect_uri=settings.spotify_redirect_uri,
        state=state,
    )
    response = RedirectResponse(authorize_url, status_code=302)
    response.set_cookie(
        STATE_COOKIE_NAME, state, httponly=True, samesite="lax", max_age=600
    )
    return response


@router.get("/callback")
async def callback(
    request: Request,
    code: str,
    state: str,
    db: sqlite3.Connection = Depends(get_db),
    client: httpx2.AsyncClient = Depends(get_http_client),
    cipher: TokenCipher = Depends(get_cipher),
) -> RedirectResponse:
    expected_state = request.cookies.get(STATE_COOKIE_NAME)
    if not expected_state or expected_state != state:
        raise HTTPException(status_code=400, detail="Invalid or missing OAuth state")

    token = await exchange_code_for_token(
        client,
        code=code,
        redirect_uri=settings.spotify_redirect_uri,
        client_id=settings.spotify_client_id,
        client_secret=settings.spotify_client_secret,
    )
    save_tokens(
        db,
        cipher,
        access_token=token["access_token"],
        refresh_token=token["refresh_token"],
        expires_in=token["expires_in"],
        scope=token.get("scope", ""),
    )
    response = RedirectResponse("/admin/spotify/status", status_code=302)
    response.delete_cookie(STATE_COOKIE_NAME)
    return response


@router.get("/status")
async def status(
    db: sqlite3.Connection = Depends(get_db),
    cipher: TokenCipher = Depends(get_cipher),
) -> dict:
    stored = load_tokens(db, cipher)
    if stored is None:
        return {"connected": False}
    return {
        "connected": True,
        "expires_at": stored.expires_at.isoformat(),
        "scope": stored.scope,
    }
