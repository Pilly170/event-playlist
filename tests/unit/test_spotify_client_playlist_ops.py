import httpx2
import pytest
from cryptography.fernet import Fernet

from app.db import get_connection, run_migrations
from app.services.crypto import TokenCipher
from app.spotify.client import get_playlist_track_uris, insert_track_into_playlist
from app.spotify.token_store import save_tokens


def _track_json(uri):
    return {
        "uri": uri,
        "name": "A Song",
        "explicit": False,
        "artists": [{"name": "An Artist"}],
        "album": {"images": []},
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
async def test_get_playlist_track_uris_returns_all_uris_from_a_single_page(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)

    async def handler(request):
        return httpx2.Response(
            200,
            json={
                "items": [
                    {"track": _track_json("spotify:track:a")},
                    {"track": _track_json("spotify:track:b")},
                ],
                "total": 2,
                "limit": 100,
                "offset": 0,
            },
        )

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))

    uris = await get_playlist_track_uris(
        conn,
        cipher,
        client,
        client_id="id",
        client_secret="secret",
        playlist_id="playlist123",
    )

    assert uris == ["spotify:track:a", "spotify:track:b"]


@pytest.mark.asyncio
async def test_get_playlist_track_uris_walks_multiple_pages(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    call_count = 0

    async def handler(request):
        nonlocal call_count
        call_count += 1
        offset = int(request.url.params["offset"])
        if offset == 0:
            return httpx2.Response(
                200,
                json={
                    "items": [{"track": _track_json("spotify:track:a")}],
                    "total": 2,
                    "limit": 1,
                    "offset": 0,
                },
            )
        return httpx2.Response(
            200,
            json={
                "items": [{"track": _track_json("spotify:track:b")}],
                "total": 2,
                "limit": 1,
                "offset": 1,
            },
        )

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))

    uris = await get_playlist_track_uris(
        conn,
        cipher,
        client,
        client_id="id",
        client_secret="secret",
        playlist_id="playlist123",
        page_size=1,
    )

    assert uris == ["spotify:track:a", "spotify:track:b"]
    assert call_count == 2


@pytest.mark.asyncio
async def test_get_playlist_track_uris_skips_null_tracks(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)

    async def handler(request):
        return httpx2.Response(
            200,
            json={
                "items": [{"track": None}, {"track": _track_json("spotify:track:a")}],
                "total": 2,
                "limit": 100,
                "offset": 0,
            },
        )

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))

    uris = await get_playlist_track_uris(
        conn,
        cipher,
        client,
        client_id="id",
        client_secret="secret",
        playlist_id="playlist123",
    )

    assert uris == ["spotify:track:a"]


@pytest.mark.asyncio
async def test_insert_track_into_playlist_sends_uri_and_position_returns_snapshot(
    tmp_path,
):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    captured = {}

    async def handler(request):
        captured["url"] = str(request.url)
        captured["body"] = request.read().decode()
        return httpx2.Response(200, json={"snapshot_id": "snap-123"})

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))

    snapshot_id = await insert_track_into_playlist(
        conn,
        cipher,
        client,
        client_id="id",
        client_secret="secret",
        playlist_id="playlist123",
        track_uri="spotify:track:abc123",
        position=5,
    )

    assert "playlists/playlist123/tracks" in captured["url"]
    assert (
        "spotify%3Atrack%3Aabc123" in captured["body"]
        or "spotify:track:abc123" in captured["body"]
    )
    assert '"position": 5' in captured["body"] or '"position":5' in captured["body"]
    assert snapshot_id == "snap-123"
