from urllib.parse import parse_qs, urlparse

import httpx2
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.db import get_connection, run_migrations
from app.dependencies import get_cipher, get_db, get_http_client
from app.main import app
from app.security.session import require_onboarded_admin
from app.services.crypto import TokenCipher


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
    # These tests exercise the Spotify OAuth flow, not admin auth — that's covered
    # separately in test_admin_auth_routes.py, so the auth gate is stubbed out here.
    app.dependency_overrides[require_onboarded_admin] = lambda: "admin"
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_connect_redirects_to_spotify_and_sets_state_cookie(client):
    response = client.get("/admin/spotify/connect", follow_redirects=False)

    assert response.status_code == 302
    location = urlparse(response.headers["location"])
    assert location.netloc == "accounts.spotify.com"
    state_param = parse_qs(location.query)["state"][0]
    assert response.cookies["spotify_oauth_state"] == state_param


def test_callback_rejects_mismatched_state(client):
    client.get("/admin/spotify/connect", follow_redirects=False)

    response = client.get(
        "/admin/spotify/callback",
        params={"code": "auth-code", "state": "wrong-state"},
        follow_redirects=False,
    )

    assert response.status_code == 400


def test_callback_exchanges_code_and_status_then_reports_connected(client, cipher):
    async def handler(request):
        return httpx2.Response(
            200,
            json={
                "access_token": "access-1",
                "refresh_token": "refresh-1",
                "expires_in": 3600,
                "scope": "playlist-read-private",
            },
        )

    app.dependency_overrides[get_http_client] = lambda: httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler)
    )

    connect_response = client.get("/admin/spotify/connect", follow_redirects=False)
    state = urlparse(connect_response.headers["location"]).query
    state_value = parse_qs(state)["state"][0]

    callback_response = client.get(
        "/admin/spotify/callback",
        params={"code": "auth-code", "state": state_value},
        follow_redirects=False,
    )
    assert callback_response.status_code == 302

    status_response = client.get("/admin/spotify/status")
    body = status_response.json()
    assert body["connected"] is True
    assert body["scope"] == "playlist-read-private"
    assert "expires_at" in body


def test_status_reports_not_connected_when_no_tokens_stored(client):
    response = client.get("/admin/spotify/status")

    assert response.json() == {"connected": False}
