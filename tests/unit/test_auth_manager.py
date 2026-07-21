import httpx2
import pytest
from cryptography.fernet import Fernet

from app.db import get_connection, run_migrations
from app.services.crypto import TokenCipher
from app.spotify.auth_manager import SpotifyNotConnectedError, get_valid_access_token
from app.spotify.token_store import load_tokens, save_tokens


def _connection(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    run_migrations(conn)
    return conn


def _client(handler):
    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


@pytest.mark.asyncio
async def test_raises_when_no_account_connected(tmp_path):
    conn = _connection(tmp_path)
    cipher = TokenCipher(key=Fernet.generate_key().decode())

    async def handler(request):
        raise AssertionError("should not make an HTTP call")

    with pytest.raises(SpotifyNotConnectedError):
        await get_valid_access_token(
            conn, cipher, _client(handler), client_id="id", client_secret="secret"
        )


@pytest.mark.asyncio
async def test_returns_cached_access_token_without_refreshing_when_not_expired(
    tmp_path,
):
    conn = _connection(tmp_path)
    cipher = TokenCipher(key=Fernet.generate_key().decode())
    save_tokens(
        conn,
        cipher,
        access_token="still-valid",
        refresh_token="refresh-1",
        expires_in=3600,
        scope="s",
    )

    async def handler(request):
        raise AssertionError("should not refresh a token that isn't expired")

    token = await get_valid_access_token(
        conn, cipher, _client(handler), client_id="id", client_secret="secret"
    )

    assert token == "still-valid"


@pytest.mark.asyncio
async def test_refreshes_and_persists_new_access_token_when_expired(tmp_path):
    conn = _connection(tmp_path)
    cipher = TokenCipher(key=Fernet.generate_key().decode())
    save_tokens(
        conn,
        cipher,
        access_token="expired-token",
        refresh_token="refresh-1",
        expires_in=-100,
        scope="s",
    )

    async def handler(request):
        return httpx2.Response(
            200,
            json={"access_token": "refreshed-token", "expires_in": 3600, "scope": "s"},
        )

    token = await get_valid_access_token(
        conn, cipher, _client(handler), client_id="id", client_secret="secret"
    )

    assert token == "refreshed-token"
    stored = load_tokens(conn, cipher)
    assert stored.access_token == "refreshed-token"


@pytest.mark.asyncio
async def test_preserves_existing_refresh_token_when_refresh_response_omits_one(
    tmp_path,
):
    conn = _connection(tmp_path)
    cipher = TokenCipher(key=Fernet.generate_key().decode())
    save_tokens(
        conn,
        cipher,
        access_token="expired-token",
        refresh_token="original-refresh",
        expires_in=-100,
        scope="s",
    )

    async def handler(request):
        return httpx2.Response(
            200,
            json={"access_token": "refreshed-token", "expires_in": 3600, "scope": "s"},
        )

    await get_valid_access_token(
        conn, cipher, _client(handler), client_id="id", client_secret="secret"
    )

    stored = load_tokens(conn, cipher)
    assert stored.refresh_token == "original-refresh"
