import httpx2
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.db import get_connection, run_migrations
from app.dependencies import get_cipher, get_db, get_http_client
from app.main import app
from app.models.config import update_config
from app.models.requests import create_request
from app.services.crypto import TokenCipher
from app.spotify.token_store import save_tokens

TRACK_JSON = {
    "uri": "spotify:track:abc123",
    "name": "A Song",
    "explicit": False,
    "artists": [{"name": "An Artist"}],
    "album": {"images": [{"url": "https://images.example/cover.jpg"}]},
}


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    conn = get_connection(path)
    run_migrations(conn)
    conn.close()
    return path


@pytest.fixture
def cipher():
    return TokenCipher(key=Fernet.generate_key().decode())


def _connect_spotify(db_path, cipher):
    conn = get_connection(db_path)
    save_tokens(
        conn,
        cipher,
        access_token="valid-token",
        refresh_token="r",
        expires_in=3600,
        scope="s",
    )
    conn.close()


@pytest.fixture
def client(db_path, cipher):
    async def override_get_db():
        conn = get_connection(db_path)
        try:
            yield conn
        finally:
            conn.close()

    async def unexpected_http_call(request):
        raise AssertionError(f"unexpected HTTP call to {request.url}")

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_cipher] = lambda: cipher
    app.dependency_overrides[get_http_client] = lambda: httpx2.AsyncClient(
        transport=httpx2.MockTransport(unexpected_http_call)
    )
    yield TestClient(app)
    app.dependency_overrides.clear()


def _mock_spotify(json_response=None, *, status_code=200):
    async def handler(request):
        return httpx2.Response(status_code, json=json_response)

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


def test_menu_shows_friendly_message_when_spotify_not_connected(client):
    response = client.get("/menu")

    assert response.status_code == 200
    assert "temporarily unavailable" in response.text


def test_menu_shows_nothing_playing(client, db_path, cipher):
    _connect_spotify(db_path, cipher)
    app.dependency_overrides[get_http_client] = lambda: _mock_spotify(status_code=204)

    response = client.get("/menu")

    assert "Nothing is currently playing" in response.text


def test_menu_shows_currently_playing_track(client, db_path, cipher):
    _connect_spotify(db_path, cipher)
    app.dependency_overrides[get_http_client] = lambda: _mock_spotify(
        {"is_playing": True, "item": TRACK_JSON}
    )

    response = client.get("/menu")

    assert "A Song" in response.text
    assert "An Artist" in response.text
    assert "(paused)" not in response.text


def test_menu_shows_paused_indicator(client, db_path, cipher):
    _connect_spotify(db_path, cipher)
    app.dependency_overrides[get_http_client] = lambda: _mock_spotify(
        {"is_playing": False, "item": TRACK_JSON}
    )

    response = client.get("/menu")

    assert "(paused)" in response.text


def test_menu_without_a_code_does_not_show_a_status_result(client):
    response = client.get("/menu")

    assert "Not found" not in response.text
    assert "Status:" not in response.text


def test_menu_status_lookup_shows_pending_status(client, db_path):
    conn = get_connection(db_path)
    created = create_request(
        conn,
        spotify_track_uri="spotify:track:abc123",
        track_name="A Song",
        artist_name="An Artist",
        is_explicit=False,
        requestor_name="Alex",
        device_token="device-1",
        client_ip="1.2.3.4",
    )
    conn.close()

    response = client.get("/menu", params={"code": created.reference_code})

    assert "pending" in response.text
    assert "A Song" in response.text


def test_menu_status_lookup_is_case_insensitive(client, db_path):
    conn = get_connection(db_path)
    created = create_request(
        conn,
        spotify_track_uri="spotify:track:abc123",
        track_name="A Song",
        artist_name="An Artist",
        is_explicit=False,
        requestor_name="Alex",
        device_token="device-1",
        client_ip="1.2.3.4",
    )
    conn.close()

    response = client.get("/menu", params={"code": created.reference_code.lower()})

    assert "pending" in response.text


def test_menu_status_lookup_not_found_for_unknown_code(client):
    response = client.get("/menu", params={"code": "ZZZZZZ"})

    assert "Not found" in response.text


def test_playlist_shows_not_configured_message(client):
    response = client.get("/menu/playlist")

    assert "No playlist has been configured" in response.text


def test_playlist_shows_tracks_when_configured(client, db_path, cipher):
    _connect_spotify(db_path, cipher)
    conn = get_connection(db_path)
    update_config(conn, default_playlist_id="playlist123")
    conn.close()
    app.dependency_overrides[get_http_client] = lambda: _mock_spotify(
        {"items": [{"track": TRACK_JSON}], "total": 1, "limit": 20, "offset": 0}
    )

    response = client.get("/menu/playlist")

    assert "A Song" in response.text
    assert "Previous" not in response.text
    assert "Next" not in response.text


def test_playlist_pagination_shows_next_link_when_more_pages_exist(
    client, db_path, cipher
):
    _connect_spotify(db_path, cipher)
    conn = get_connection(db_path)
    update_config(conn, default_playlist_id="playlist123")
    conn.close()
    app.dependency_overrides[get_http_client] = lambda: _mock_spotify(
        {"items": [{"track": TRACK_JSON}], "total": 50, "limit": 20, "offset": 0}
    )

    response = client.get("/menu/playlist")

    assert "Next" in response.text
    assert "Previous" not in response.text


def test_playlist_pagination_shows_previous_link_on_later_pages(
    client, db_path, cipher
):
    _connect_spotify(db_path, cipher)
    conn = get_connection(db_path)
    update_config(conn, default_playlist_id="playlist123")
    conn.close()
    app.dependency_overrides[get_http_client] = lambda: _mock_spotify(
        {"items": [{"track": TRACK_JSON}], "total": 50, "limit": 20, "offset": 20}
    )

    response = client.get("/menu/playlist", params={"offset": 20})

    assert "Previous" in response.text
