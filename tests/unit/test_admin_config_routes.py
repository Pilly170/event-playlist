import re

import pytest
from fastapi.testclient import TestClient

from app.db import get_connection, run_migrations
from app.dependencies import get_db
from app.main import app
from app.security.session import require_onboarded_admin


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    conn = get_connection(path)
    run_migrations(conn)
    conn.close()
    return path


@pytest.fixture
def client(db_path):
    async def override_get_db():
        conn = get_connection(db_path)
        try:
            yield conn
        finally:
            conn.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_onboarded_admin] = lambda: "admin"
    yield TestClient(app)
    app.dependency_overrides.clear()


def _extract_csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "csrf_token hidden field not found in response"
    return match.group(1)


def test_config_form_renders_current_values(client):
    response = client.get("/admin/config")

    assert response.status_code == 200
    assert 'name="insert_tracks_ahead" value="3"' in response.text


def test_config_submit_persists_changes_with_a_valid_csrf_token(client, db_path):
    csrf_token = _extract_csrf_token(client.get("/admin/config").text)

    response = client.post(
        "/admin/config",
        data={
            "insert_tracks_ahead": "7",
            "poll_interval_seconds": "20",
            "csrf_token": csrf_token,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT insert_tracks_ahead, poll_interval_seconds FROM config"
    ).fetchone()
    assert row == (7, 20)


def test_config_submit_rejects_missing_csrf_token(client, db_path):
    client.get("/admin/config")

    response = client.post(
        "/admin/config",
        data={"insert_tracks_ahead": "7", "poll_interval_seconds": "20"},
    )

    assert response.status_code == 422
    conn = get_connection(db_path)
    assert conn.execute("SELECT insert_tracks_ahead FROM config").fetchone()[0] == 3


def test_config_submit_rejects_wrong_csrf_token(client, db_path):
    client.get("/admin/config")

    response = client.post(
        "/admin/config",
        data={
            "insert_tracks_ahead": "7",
            "poll_interval_seconds": "20",
            "csrf_token": "not-the-right-token",
        },
    )

    assert response.status_code == 403
    conn = get_connection(db_path)
    assert conn.execute("SELECT insert_tracks_ahead FROM config").fetchone()[0] == 3
