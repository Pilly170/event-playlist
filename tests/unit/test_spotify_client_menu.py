import httpx2
import pytest
from cryptography.fernet import Fernet

from app.db import get_connection, run_migrations
from app.services.crypto import TokenCipher
from app.spotify.client import get_now_playing, get_playlist_tracks
from app.spotify.token_store import save_tokens

TRACK_JSON = {
    "uri": "spotify:track:abc123",
    "name": "A Song",
    "explicit": False,
    "artists": [{"name": "An Artist"}],
    "album": {"images": [{"url": "https://images.example/cover.jpg"}]},
}


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
async def test_get_now_playing_returns_none_when_nothing_is_playing(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)

    async def handler(request):
        return httpx2.Response(204)

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))

    result = await get_now_playing(
        conn, cipher, client, client_id="id", client_secret="secret"
    )

    assert result is None


@pytest.mark.asyncio
async def test_get_now_playing_returns_parsed_track_and_playing_state(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)

    async def handler(request):
        return httpx2.Response(200, json={"is_playing": True, "item": TRACK_JSON})

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))

    result = await get_now_playing(
        conn, cipher, client, client_id="id", client_secret="secret"
    )

    assert result.is_playing is True
    assert result.track.name == "A Song"
    assert result.track.artist == "An Artist"


@pytest.mark.asyncio
async def test_get_now_playing_reflects_paused_state(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)

    async def handler(request):
        return httpx2.Response(200, json={"is_playing": False, "item": TRACK_JSON})

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))

    result = await get_now_playing(
        conn, cipher, client, client_id="id", client_secret="secret"
    )

    assert result.is_playing is False


@pytest.mark.asyncio
async def test_get_now_playing_returns_none_when_item_is_null(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)

    async def handler(request):
        return httpx2.Response(200, json={"is_playing": False, "item": None})

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))

    result = await get_now_playing(
        conn, cipher, client, client_id="id", client_secret="secret"
    )

    assert result is None


@pytest.mark.asyncio
async def test_get_playlist_tracks_returns_parsed_page(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    captured = {}

    async def handler(request):
        captured["url"] = str(request.url)
        return httpx2.Response(
            200,
            json={
                "items": [{"track": TRACK_JSON}],
                "total": 1,
                "limit": 20,
                "offset": 0,
            },
        )

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))

    page = await get_playlist_tracks(
        conn,
        cipher,
        client,
        client_id="id",
        client_secret="secret",
        playlist_id="playlist123",
        limit=20,
        offset=0,
    )

    assert "playlists/playlist123/tracks" in captured["url"]
    assert page.total == 1
    assert page.offset == 0
    assert page.limit == 20
    assert len(page.items) == 1
    assert page.items[0].name == "A Song"


@pytest.mark.asyncio
async def test_get_playlist_tracks_skips_null_tracks(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)

    async def handler(request):
        return httpx2.Response(
            200,
            json={
                "items": [{"track": None}, {"track": TRACK_JSON}],
                "total": 2,
                "limit": 20,
                "offset": 0,
            },
        )

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))

    page = await get_playlist_tracks(
        conn,
        cipher,
        client,
        client_id="id",
        client_secret="secret",
        playlist_id="playlist123",
    )

    assert len(page.items) == 1
