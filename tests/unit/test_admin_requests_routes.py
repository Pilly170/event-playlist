import httpx2
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.db import get_connection, run_migrations
from app.dependencies import get_cipher, get_db, get_http_client
from app.main import app
from app.models.config import update_config
from app.models.requests import create_request
from app.security.session import require_onboarded_admin
from app.services.crypto import TokenCipher
from app.spotify.token_store import save_tokens


def _track_json(uri):
    return {
        "uri": uri,
        "name": "A Song",
        "explicit": False,
        "artists": [{"name": "An Artist"}],
        "album": {"images": []},
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


@pytest.fixture
def authenticated_client(client):
    app.dependency_overrides[require_onboarded_admin] = lambda: "admin"
    yield client


def _create_pending(db_path, **overrides):
    conn = get_connection(db_path)
    defaults = dict(
        spotify_track_uri="spotify:track:new",
        track_name="New Song",
        artist_name="New Artist",
        is_explicit=False,
        requestor_name="Alex",
        device_token="device-1",
        client_ip="1.2.3.4",
    )
    defaults.update(overrides)
    created = create_request(conn, **defaults)
    conn.close()
    return created


def _mock_approval_flow(*, now_playing_uri, playlist_uris):
    async def handler(request):
        path = request.url.path
        if path.endswith("/me/player/currently-playing"):
            return httpx2.Response(
                200, json={"is_playing": True, "item": _track_json(now_playing_uri)}
            )
        if path.endswith("/tracks") and request.method == "GET":
            offset = int(request.url.params.get("offset", 0))
            limit = int(request.url.params.get("limit", 100))
            page = playlist_uris[offset : offset + limit]
            return httpx2.Response(
                200,
                json={
                    "items": [{"track": _track_json(u)} for u in page],
                    "total": len(playlist_uris),
                    "limit": limit,
                    "offset": offset,
                },
            )
        if path.endswith("/tracks") and request.method == "POST":
            return httpx2.Response(200, json={"snapshot_id": "snap-1"})
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


def test_requests_queue_redirects_to_login_when_not_authenticated(client):
    response = client.get("/admin/requests", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


def test_requests_queue_shows_pending_requests(authenticated_client, db_path):
    _create_pending(db_path)

    response = authenticated_client.get("/admin/requests")

    assert "New Song" in response.text
    assert "New Artist" in response.text


def test_approve_succeeds_and_redirects(authenticated_client, db_path, cipher):
    _connect_spotify(db_path, cipher)
    update_config_conn = get_connection(db_path)
    update_config(
        update_config_conn, default_playlist_id="playlist123", insert_tracks_ahead=2
    )
    update_config_conn.close()
    request = _create_pending(db_path)
    app.dependency_overrides[get_http_client] = lambda: _mock_approval_flow(
        now_playing_uri="spotify:track:current",
        playlist_uris=[
            "spotify:track:zero",
            "spotify:track:current",
            "spotify:track:two",
        ],
    )

    response = authenticated_client.post(
        f"/admin/requests/{request.id}/approve", follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/requests"

    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT status, playlist_insert_position FROM requests WHERE id = ?",
        (request.id,),
    ).fetchone()
    assert row == ("added", 3)


def test_approve_shows_error_when_no_playlist_configured(authenticated_client, db_path):
    request = _create_pending(db_path)

    response = authenticated_client.post(
        f"/admin/requests/{request.id}/approve", follow_redirects=True
    )

    assert "No default playlist" in response.text
    conn = get_connection(db_path)
    status = conn.execute(
        "SELECT status FROM requests WHERE id = ?", (request.id,)
    ).fetchone()[0]
    assert status == "pending"


def test_deny_removes_request_and_redirects(authenticated_client, db_path):
    request = _create_pending(db_path)

    response = authenticated_client.post(
        f"/admin/requests/{request.id}/deny", follow_redirects=False
    )

    assert response.status_code == 303
    conn = get_connection(db_path)
    assert conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 0
