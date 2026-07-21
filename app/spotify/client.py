import sqlite3

import httpx2

from app.services.crypto import TokenCipher
from app.spotify.auth_manager import get_valid_access_token

API_BASE_URL = "https://api.spotify.com/v1"


async def get_currently_playing(
    conn: sqlite3.Connection,
    cipher: TokenCipher,
    client: httpx2.AsyncClient,
    *,
    client_id: str,
    client_secret: str,
) -> dict | None:
    access_token = await get_valid_access_token(
        conn, cipher, client, client_id=client_id, client_secret=client_secret
    )
    response = await client.get(
        f"{API_BASE_URL}/me/player/currently-playing",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if response.status_code == 204:
        return None
    response.raise_for_status()
    return response.json()
