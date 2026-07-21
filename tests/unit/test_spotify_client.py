import httpx2
import pytest
from cryptography.fernet import Fernet

from app.db import get_connection, run_migrations
from app.services.crypto import TokenCipher
from app.spotify.client import get_currently_playing
from app.spotify.token_store import save_tokens


def _connection(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    run_migrations(conn)
    return conn


def _connected_cipher(conn):
    cipher = TokenCipher(key=Fernet.generate_key().decode())
    save_tokens(
        conn,
        cipher,
        access_token="valid-access-token",
        refresh_token="r",
        expires_in=3600,
        scope="s",
    )
    return cipher


@pytest.mark.asyncio
async def test_sends_bearer_token_and_returns_currently_playing_track(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    captured = {}

    async def handler(request):
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        return httpx2.Response(
            200, json={"is_playing": True, "item": {"name": "A Song"}}
        )

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))

    result = await get_currently_playing(
        conn, cipher, client, client_id="id", client_secret="secret"
    )

    assert captured["url"] == "https://api.spotify.com/v1/me/player/currently-playing"
    assert captured["authorization"] == "Bearer valid-access-token"
    assert result == {"is_playing": True, "item": {"name": "A Song"}}


@pytest.mark.asyncio
async def test_returns_none_when_nothing_currently_playing(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)

    async def handler(request):
        return httpx2.Response(204)

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))

    result = await get_currently_playing(
        conn, cipher, client, client_id="id", client_secret="secret"
    )

    assert result is None
