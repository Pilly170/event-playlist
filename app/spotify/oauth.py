from urllib.parse import urlencode

import httpx2

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"

REQUIRED_SCOPES = [
    "playlist-read-private",
    "playlist-modify-private",
    "playlist-modify-public",
    "user-read-currently-playing",
    "user-read-playback-state",
    "user-modify-playback-state",
]


def build_authorize_url(*, client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": " ".join(REQUIRED_SCOPES),
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code_for_token(
    client: httpx2.AsyncClient,
    *,
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
) -> dict:
    response = await client.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
        auth=(client_id, client_secret),
    )
    response.raise_for_status()
    return response.json()


async def refresh_access_token(
    client: httpx2.AsyncClient,
    *,
    refresh_token: str,
    client_id: str,
    client_secret: str,
) -> dict:
    response = await client.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        auth=(client_id, client_secret),
    )
    response.raise_for_status()
    return response.json()
