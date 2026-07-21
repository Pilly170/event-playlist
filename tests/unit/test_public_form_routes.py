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
EXPLICIT_TRACK_JSON = {
    **TRACK_JSON,
    "uri": "spotify:track:explicit1",
    "name": "A Rude Song",
    "explicit": True,
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


def _unexpected_http_call(client_override_target):
    async def handler(request):
        raise AssertionError(f"unexpected HTTP call to {request.url}")

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


@pytest.fixture
def client(db_path, cipher):
    async def override_get_db():
        conn = get_connection(db_path)
        try:
            yield conn
        finally:
            conn.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_cipher] = lambda: cipher
    app.dependency_overrides[get_http_client] = lambda: _unexpected_http_call(None)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _mock_spotify(json_response, *, status_code=200):
    async def handler(request):
        return httpx2.Response(status_code, json=json_response)

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


def test_request_form_page_renders(client):
    response = client.get("/request")

    assert response.status_code == 200
    assert "search" in response.text.lower()


def test_search_with_blank_query_returns_empty_without_calling_spotify(client):
    response = client.get("/request/search", params={"q": ""})

    assert response.status_code == 200
    assert "No results" in response.text


def test_search_returns_matching_tracks(client, db_path, cipher):
    _connect_spotify(db_path, cipher)
    app.dependency_overrides[get_http_client] = lambda: _mock_spotify(
        {"tracks": {"items": [TRACK_JSON]}}
    )

    response = client.get("/request/search", params={"q": "a song"})

    assert response.status_code == 200
    assert "A Song" in response.text
    assert "An Artist" in response.text


def test_search_filters_explicit_tracks_when_config_excludes_them(
    client, db_path, cipher
):
    _connect_spotify(db_path, cipher)
    conn = get_connection(db_path)
    update_config(conn, exclude_explicit=True)
    conn.close()
    app.dependency_overrides[get_http_client] = lambda: _mock_spotify(
        {"tracks": {"items": [TRACK_JSON, EXPLICIT_TRACK_JSON]}}
    )

    response = client.get("/request/search", params={"q": "song"})

    assert "A Song" in response.text
    assert "A Rude Song" not in response.text


def test_search_shows_friendly_message_when_spotify_not_connected(client):
    response = client.get("/request/search", params={"q": "anything"})

    assert response.status_code == 200
    assert "temporarily unavailable" in response.text


def test_select_renders_confirmation_with_authoritative_track_data(
    client, db_path, cipher
):
    _connect_spotify(db_path, cipher)
    app.dependency_overrides[get_http_client] = lambda: _mock_spotify(TRACK_JSON)

    response = client.post(
        "/request/select", data={"spotify_track_uri": "spotify:track:abc123"}
    )

    assert response.status_code == 200
    assert "A Song" in response.text
    assert "requestor_name" in response.text


def test_select_rejects_explicit_track_when_config_excludes_them(
    client, db_path, cipher
):
    _connect_spotify(db_path, cipher)
    conn = get_connection(db_path)
    update_config(conn, exclude_explicit=True)
    conn.close()
    app.dependency_overrides[get_http_client] = lambda: _mock_spotify(
        EXPLICIT_TRACK_JSON
    )

    response = client.post(
        "/request/select", data={"spotify_track_uri": "spotify:track:explicit1"}
    )

    assert "be requested" in response.text


def test_select_shows_error_for_unknown_track(client, db_path, cipher):
    _connect_spotify(db_path, cipher)
    app.dependency_overrides[get_http_client] = lambda: _mock_spotify(
        {"error": "not found"}, status_code=404
    )

    response = client.post(
        "/request/select", data={"spotify_track_uri": "spotify:track:missing"}
    )

    assert "couldn" in response.text and "be found" in response.text


def test_submit_creates_pending_request_and_shows_reference_code(
    client, db_path, cipher
):
    _connect_spotify(db_path, cipher)
    app.dependency_overrides[get_http_client] = lambda: _mock_spotify(TRACK_JSON)

    response = client.post(
        "/request/submit",
        data={"spotify_track_uri": "spotify:track:abc123", "requestor_name": "Alex"},
    )

    assert response.status_code == 200
    assert "reference code" in response.text.lower()

    conn = get_connection(db_path)
    row = conn.execute("SELECT status, requestor_name FROM requests").fetchone()
    assert row == ("pending", "Alex")


def test_submit_sets_a_device_token_cookie(client, db_path, cipher):
    _connect_spotify(db_path, cipher)
    app.dependency_overrides[get_http_client] = lambda: _mock_spotify(TRACK_JSON)

    response = client.post(
        "/request/submit",
        data={"spotify_track_uri": "spotify:track:abc123", "requestor_name": "Alex"},
    )

    assert "device_token" in response.cookies


def test_submit_rejects_duplicate_active_request(client, db_path, cipher):
    _connect_spotify(db_path, cipher)
    conn = get_connection(db_path)
    create_request(
        conn,
        spotify_track_uri="spotify:track:abc123",
        track_name="A Song",
        artist_name="An Artist",
        is_explicit=False,
        requestor_name="Someone",
        device_token="other-device",
        client_ip="9.9.9.9",
    )
    conn.close()
    app.dependency_overrides[get_http_client] = lambda: _mock_spotify(TRACK_JSON)

    response = client.post(
        "/request/submit",
        data={"spotify_track_uri": "spotify:track:abc123", "requestor_name": "Alex"},
    )

    assert "Already queued" in response.text
    conn = get_connection(db_path)
    assert conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 1


def test_submit_rejects_when_rate_limited(client, db_path, cipher):
    _connect_spotify(db_path, cipher)
    conn = get_connection(db_path)
    for i in range(5):
        create_request(
            conn,
            spotify_track_uri=f"spotify:track:{i}",
            track_name="Song",
            artist_name="Artist",
            is_explicit=False,
            requestor_name="Someone",
            device_token="rate-limited-device",
            client_ip=f"1.1.1.{i}",
        )
    conn.close()
    client.cookies.set("device_token", "rate-limited-device")
    app.dependency_overrides[get_http_client] = lambda: _mock_spotify(TRACK_JSON)

    response = client.post(
        "/request/submit",
        data={"spotify_track_uri": "spotify:track:abc123", "requestor_name": "Alex"},
    )

    assert "too many requests" in response.text.lower()
    conn = get_connection(db_path)
    assert conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 5


def test_submit_with_honeypot_filled_does_not_create_a_request(client, db_path, cipher):
    _connect_spotify(db_path, cipher)

    response = client.post(
        "/request/submit",
        data={
            "spotify_track_uri": "spotify:track:abc123",
            "requestor_name": "Alex",
            "hp_confirm": "I am a bot",
        },
    )

    assert response.status_code == 200
    conn = get_connection(db_path)
    assert conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 0


def test_submit_rejects_explicit_track_when_config_excludes_them(
    client, db_path, cipher
):
    _connect_spotify(db_path, cipher)
    conn = get_connection(db_path)
    update_config(conn, exclude_explicit=True)
    conn.close()
    app.dependency_overrides[get_http_client] = lambda: _mock_spotify(
        EXPLICIT_TRACK_JSON
    )

    response = client.post(
        "/request/submit",
        data={"spotify_track_uri": "spotify:track:explicit1", "requestor_name": "Alex"},
    )

    assert "be requested" in response.text
    conn = get_connection(db_path)
    assert conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 0


def test_search_rejects_an_excessively_long_query(client):
    response = client.get("/request/search", params={"q": "x" * 101})

    assert response.status_code == 422
