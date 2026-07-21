import sqlite3
from datetime import datetime, timedelta, timezone

import httpx2

from app.services.crypto import TokenCipher
from app.spotify.oauth import refresh_access_token
from app.spotify.token_store import load_tokens, save_tokens

EXPIRY_SAFETY_MARGIN = timedelta(seconds=60)


class SpotifyNotConnectedError(Exception):
    pass


async def get_valid_access_token(
    conn: sqlite3.Connection,
    cipher: TokenCipher,
    client: httpx2.AsyncClient,
    *,
    client_id: str,
    client_secret: str,
) -> str:
    stored = load_tokens(conn, cipher)
    if stored is None:
        raise SpotifyNotConnectedError("No Spotify account connected")

    if stored.expires_at - EXPIRY_SAFETY_MARGIN > datetime.now(timezone.utc):
        return stored.access_token

    token_response = await refresh_access_token(
        client,
        refresh_token=stored.refresh_token,
        client_id=client_id,
        client_secret=client_secret,
    )
    save_tokens(
        conn,
        cipher,
        access_token=token_response["access_token"],
        refresh_token=token_response.get("refresh_token", stored.refresh_token),
        expires_in=token_response["expires_in"],
        scope=token_response.get("scope", stored.scope),
    )
    return token_response["access_token"]
