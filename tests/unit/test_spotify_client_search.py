import httpx2
import pytest
from cryptography.fernet import Fernet

from app.db import get_connection, run_migrations
from app.services.crypto import TokenCipher
from app.spotify.client import get_track, search_tracks
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
async def test_search_tracks_returns_parsed_results(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    captured = {}

    async def handler(request):
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        return httpx2.Response(200, json={"tracks": {"items": [TRACK_JSON]}})

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))

    results = await search_tracks(
        conn, cipher, client, client_id="id", client_secret="secret", query="a song"
    )

    assert "q=a+song" in captured["url"] or "q=a%20song" in captured["url"]
    assert "type=track" in captured["url"]
    assert captured["authorization"] == "Bearer valid-token"
    assert len(results) == 1
    assert results[0].uri == "spotify:track:abc123"
    assert results[0].name == "A Song"
    assert results[0].artist == "An Artist"
    assert results[0].album_image_url == "https://images.example/cover.jpg"
    assert results[0].is_explicit is False


@pytest.mark.asyncio
async def test_search_tracks_joins_multiple_artists(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    track = {**TRACK_JSON, "artists": [{"name": "Artist One"}, {"name": "Artist Two"}]}

    async def handler(request):
        return httpx2.Response(200, json={"tracks": {"items": [track]}})

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))

    results = await search_tracks(
        conn, cipher, client, client_id="id", client_secret="secret", query="x"
    )

    assert results[0].artist == "Artist One, Artist Two"


@pytest.mark.asyncio
async def test_search_tracks_handles_missing_album_art(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    track = {**TRACK_JSON, "album": {"images": []}}

    async def handler(request):
        return httpx2.Response(200, json={"tracks": {"items": [track]}})

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))

    results = await search_tracks(
        conn, cipher, client, client_id="id", client_secret="secret", query="x"
    )

    assert results[0].album_image_url is None


@pytest.mark.asyncio
async def test_get_track_returns_parsed_result_for_a_valid_uri(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    captured = {}

    async def handler(request):
        captured["url"] = str(request.url)
        return httpx2.Response(200, json=TRACK_JSON)

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))

    result = await get_track(
        conn,
        cipher,
        client,
        client_id="id",
        client_secret="secret",
        track_uri="spotify:track:abc123",
    )

    assert captured["url"] == "https://api.spotify.com/v1/tracks/abc123"
    assert result.uri == "spotify:track:abc123"


@pytest.mark.asyncio
async def test_get_track_returns_none_for_a_404(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)

    async def handler(request):
        return httpx2.Response(
            404, json={"error": {"status": 404, "message": "not found"}}
        )

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))

    result = await get_track(
        conn,
        cipher,
        client,
        client_id="id",
        client_secret="secret",
        track_uri="spotify:track:doesnotexist",
    )

    assert result is None
