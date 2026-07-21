import httpx2
import pytest
from cryptography.fernet import Fernet

from app.db import get_connection, run_migrations
from app.services.crypto import TokenCipher
from app.spotify.client import delete_track_from_playlist, set_repeat_mode
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
        access_token="valid-token",
        refresh_token="r",
        expires_in=3600,
        scope="s",
    )
    return cipher


@pytest.mark.asyncio
async def test_set_repeat_mode_enabled_requests_context_state(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    captured = {}

    async def handler(request):
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        return httpx2.Response(204)

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))

    await set_repeat_mode(
        conn, cipher, client, client_id="id", client_secret="secret", enabled=True
    )

    assert "me/player/repeat" in captured["url"]
    assert "state=context" in captured["url"]
    assert captured["authorization"] == "Bearer valid-token"


@pytest.mark.asyncio
async def test_set_repeat_mode_disabled_requests_off_state(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    captured = {}

    async def handler(request):
        captured["url"] = str(request.url)
        return httpx2.Response(204)

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))

    await set_repeat_mode(
        conn, cipher, client, client_id="id", client_secret="secret", enabled=False
    )

    assert "state=off" in captured["url"]


@pytest.mark.asyncio
async def test_delete_track_from_playlist_sends_uri_and_position_returns_snapshot(
    tmp_path,
):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    captured = {}

    async def handler(request):
        captured["url"] = str(request.url)
        captured["body"] = request.read().decode()
        captured["method"] = request.method
        return httpx2.Response(200, json={"snapshot_id": "snap-after-remove"})

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))

    snapshot_id = await delete_track_from_playlist(
        conn,
        cipher,
        client,
        client_id="id",
        client_secret="secret",
        playlist_id="playlist123",
        track_uri="spotify:track:abc123",
        position=7,
    )

    assert captured["method"] == "DELETE"
    assert "playlists/playlist123/tracks" in captured["url"]
    assert (
        "spotify:track:abc123" in captured["body"]
        or "spotify%3Atrack%3Aabc123" in captured["body"]
    )
    assert "7" in captured["body"]
    assert snapshot_id == "snap-after-remove"
