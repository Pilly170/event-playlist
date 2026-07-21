import sqlite3

import httpx2
from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.dependencies import get_cipher, get_db, get_http_client
from app.models.config import get_config
from app.models.requests import DuplicateActiveRequestError, create_request
from app.security.device_token import (
    get_or_generate_device_token,
    set_device_token_cookie,
)
from app.services.crypto import TokenCipher
from app.services.rate_limit import check_rate_limit
from app.spotify.auth_manager import SpotifyNotConnectedError
from app.spotify.client import get_track, search_tracks

router = APIRouter(prefix="/request")
templates = Jinja2Templates(directory="app/templates")


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get("")
async def request_form(request: Request) -> Response:
    return templates.TemplateResponse(request, "public/form.html")


@router.get("/search")
async def search(
    request: Request,
    q: str = "",
    db: sqlite3.Connection = Depends(get_db),
    cipher: TokenCipher = Depends(get_cipher),
    client: httpx2.AsyncClient = Depends(get_http_client),
) -> Response:
    query = q.strip()
    if not query:
        return templates.TemplateResponse(
            request, "public/_search_results.html", {"results": []}
        )

    try:
        results = await search_tracks(
            db,
            cipher,
            client,
            client_id=settings.spotify_client_id,
            client_secret=settings.spotify_client_secret,
            query=query,
        )
    except SpotifyNotConnectedError:
        return templates.TemplateResponse(
            request,
            "public/_search_results.html",
            {"results": [], "error": "Search is temporarily unavailable."},
        )

    config = get_config(db)
    if config.exclude_explicit:
        results = [track for track in results if not track.is_explicit]

    return templates.TemplateResponse(
        request, "public/_search_results.html", {"results": results}
    )


@router.post("/select")
async def select(
    request: Request,
    spotify_track_uri: str = Form(...),
    db: sqlite3.Connection = Depends(get_db),
    cipher: TokenCipher = Depends(get_cipher),
    client: httpx2.AsyncClient = Depends(get_http_client),
) -> Response:
    # Always re-fetched from Spotify server-side, never trusted from client-supplied
    # hidden fields — the app never inserts data Spotify itself didn't authoritatively
    # return for this exact URI (SPEC.md §8).
    track = await get_track(
        db,
        cipher,
        client,
        client_id=settings.spotify_client_id,
        client_secret=settings.spotify_client_secret,
        track_uri=spotify_track_uri,
    )
    if track is None:
        return templates.TemplateResponse(
            request, "public/_error.html", {"message": "That track couldn't be found."}
        )

    config = get_config(db)
    if config.exclude_explicit and track.is_explicit:
        return templates.TemplateResponse(
            request, "public/_error.html", {"message": "That track can't be requested."}
        )

    return templates.TemplateResponse(request, "public/_confirm.html", {"track": track})


@router.post("/submit")
async def submit(
    request: Request,
    spotify_track_uri: str = Form(...),
    requestor_name: str = Form(..., max_length=80),
    hp_confirm: str = Form(""),  # honeypot — real users never see or fill this field
    db: sqlite3.Connection = Depends(get_db),
    cipher: TokenCipher = Depends(get_cipher),
    client: httpx2.AsyncClient = Depends(get_http_client),
) -> Response:
    device_token = get_or_generate_device_token(request)

    def respond(template: str, context: dict) -> Response:
        result = templates.TemplateResponse(request, template, context)
        set_device_token_cookie(result, device_token)
        return result

    if hp_confirm:
        # Bot filled the honeypot — pretend success without creating a request,
        # rather than tipping it off that it was caught.
        return respond("public/_success.html", {"reference_code": None})

    client_ip = _client_ip(request)

    rate_limit_result = check_rate_limit(
        db, device_token=device_token, client_ip=client_ip
    )
    if not rate_limit_result.allowed:
        return respond(
            "public/_error.html",
            {"message": "You've made too many requests recently. Try again later."},
        )

    track = await get_track(
        db,
        cipher,
        client,
        client_id=settings.spotify_client_id,
        client_secret=settings.spotify_client_secret,
        track_uri=spotify_track_uri,
    )
    if track is None:
        return respond(
            "public/_error.html", {"message": "That track couldn't be found."}
        )

    config = get_config(db)
    if config.exclude_explicit and track.is_explicit:
        return respond(
            "public/_error.html", {"message": "That track can't be requested."}
        )

    # require_admin_approval's "off" position (auto-add without a human) has no
    # implementation yet — the playlist-insertion mechanism it would trigger doesn't
    # exist until Phase 5. Every request lands as `pending` regardless of this config
    # value for now; see CLAUDE.md.
    try:
        created = create_request(
            db,
            spotify_track_uri=track.uri,
            track_name=track.name,
            artist_name=track.artist,
            is_explicit=track.is_explicit,
            requestor_name=requestor_name.strip(),
            device_token=device_token,
            client_ip=client_ip,
        )
    except DuplicateActiveRequestError:
        return respond(
            "public/_error.html",
            {"message": "Already queued — check the menu to see it."},
        )

    return respond("public/_success.html", {"reference_code": created.reference_code})
