import pytest
from fastapi.testclient import TestClient

from app.db import get_connection, run_migrations
from app.dependencies import get_db
from app.main import app
from app.models.admin_users import create_admin_user, get_by_username
from app.security.auth import hash_password


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

    change_response = client.post(
        "/admin/change-password",
        data={"new_password": "brand-new-password"},
        follow_redirects=False,
    )
    assert change_response.status_code == 303
    assert change_response.headers["location"] == "/admin/config"

    config_response = client.get("/admin/config")
    assert config_response.status_code == 200

    conn = get_connection(db_path)
    user = get_by_username(conn, "admin")
    assert user.last_login_at is not None


def test_logout_clears_session(client, db_path):
    _seed_admin(db_path, onboarded=True)
    client.post("/admin/login", data={"username": "admin", "password": "seed-password"})

    client.post("/admin/logout")

    response = client.get("/admin/config", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"
