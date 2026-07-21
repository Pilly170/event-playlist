import re

import pytest
from fastapi.testclient import TestClient

from app.db import get_connection, run_migrations
from app.dependencies import get_database_path, get_db
from app.main import app
from app.models.admin_users import create_admin_user, get_by_username
from app.security.auth import hash_password
from app.services.admin_seed import initial_admin_password_path


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
    app.dependency_overrides[get_database_path] = lambda: db_path
    yield TestClient(app)
    app.dependency_overrides.clear()


def _seed_admin(
    db_path, *, username="admin", password="seed-password", onboarded=False
):
    conn = get_connection(db_path)
    create_admin_user(conn, username=username, password_hash=hash_password(password))
    if onboarded:
        conn.execute(
            "UPDATE admin_users SET last_login_at = datetime('now') WHERE username = ?",
            (username,),
        )
        conn.commit()
    conn.close()


def _extract_csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "csrf_token hidden field not found in response"
    return match.group(1)


def test_login_form_renders(client):
    response = client.get("/admin/login")

    assert response.status_code == 200
    assert "form" in response.text.lower()


def test_login_with_wrong_password_returns_401_and_does_not_set_session(
    client, db_path
):
    _seed_admin(db_path)

    response = client.post(
        "/admin/login", data={"username": "admin", "password": "wrong"}
    )

    assert response.status_code == 401
    assert "session" not in response.cookies


def test_login_with_correct_password_but_never_onboarded_redirects_to_change_password(
    client, db_path
):
    _seed_admin(db_path, onboarded=False)

    response = client.post(
        "/admin/login",
        data={"username": "admin", "password": "seed-password"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/change-password"


def test_login_with_correct_password_when_already_onboarded_redirects_to_config(
    client, db_path
):
    _seed_admin(db_path, onboarded=True)

    response = client.post(
        "/admin/login",
        data={"username": "admin", "password": "seed-password"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/config"


def test_config_page_redirects_to_login_when_not_authenticated(client):
    response = client.get("/admin/config", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


def test_config_page_redirects_to_change_password_when_not_yet_onboarded(
    client, db_path
):
    _seed_admin(db_path, onboarded=False)
    client.post("/admin/login", data={"username": "admin", "password": "seed-password"})

    response = client.get("/admin/config", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/change-password"


def test_completing_password_change_marks_admin_onboarded_and_allows_config_access(
    client, db_path
):
    _seed_admin(db_path, onboarded=False)
    client.post("/admin/login", data={"username": "admin", "password": "seed-password"})
    csrf_token = _extract_csrf_token(client.get("/admin/change-password").text)

    change_response = client.post(
        "/admin/change-password",
        data={"new_password": "brand-new-password", "csrf_token": csrf_token},
        follow_redirects=False,
    )
    assert change_response.status_code == 303
    assert change_response.headers["location"] == "/admin/config"

    config_response = client.get("/admin/config")
    assert config_response.status_code == 200

    conn = get_connection(db_path)
    user = get_by_username(conn, "admin")
    assert user.last_login_at is not None


def test_completing_password_change_deletes_the_initial_password_file(client, db_path):
    _seed_admin(db_path, onboarded=False)
    password_file = initial_admin_password_path(db_path)
    password_file.parent.mkdir(parents=True, exist_ok=True)
    password_file.write_text("seed-password\n")
    client.post("/admin/login", data={"username": "admin", "password": "seed-password"})
    csrf_token = _extract_csrf_token(client.get("/admin/change-password").text)

    client.post(
        "/admin/change-password",
        data={"new_password": "brand-new-password", "csrf_token": csrf_token},
    )

    assert not password_file.exists()


def test_logout_clears_session(client, db_path):
    _seed_admin(db_path, onboarded=True)
    client.post("/admin/login", data={"username": "admin", "password": "seed-password"})
    csrf_token = _extract_csrf_token(client.get("/admin/config").text)

    client.post("/admin/logout", data={"csrf_token": csrf_token})

    response = client.get("/admin/config", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


def test_change_password_rejects_missing_csrf_token(client, db_path):
    _seed_admin(db_path, onboarded=True)
    client.post("/admin/login", data={"username": "admin", "password": "seed-password"})

    response = client.post(
        "/admin/change-password", data={"new_password": "brand-new-password"}
    )

    assert response.status_code == 422


def test_logout_rejects_wrong_csrf_token(client, db_path):
    _seed_admin(db_path, onboarded=True)
    client.post("/admin/login", data={"username": "admin", "password": "seed-password"})
    client.get("/admin/config")

    response = client.post("/admin/logout", data={"csrf_token": "not-the-right-token"})

    assert response.status_code == 403
    # session must still be intact — a forged logout must not have gone through
    page = client.get("/admin/config", follow_redirects=False)
    assert page.status_code == 200


def test_login_is_locked_out_after_repeated_failures(client, db_path):
    _seed_admin(db_path, onboarded=True)
    for _ in range(5):
        client.post("/admin/login", data={"username": "admin", "password": "wrong"})

    response = client.post(
        "/admin/login", data={"username": "admin", "password": "seed-password"}
    )

    assert response.status_code == 429
    assert "Too many failed login attempts" in response.text

    conn = get_connection(db_path)
    blocked_entries = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action = 'login.blocked' AND actor = 'admin'"
    ).fetchone()[0]
    assert blocked_entries == 1
