from app.db import get_connection, run_migrations
from app.models.admin_users import (
    count_admin_users,
    create_admin_user,
    get_by_username,
    record_login,
    update_password,
)


def _connection(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    run_migrations(conn)
    return conn


def test_count_admin_users_is_zero_before_any_are_created(tmp_path):
    conn = _connection(tmp_path)

    assert count_admin_users(conn) == 0


def test_create_admin_user_then_get_by_username_round_trips(tmp_path):
    conn = _connection(tmp_path)

    created = create_admin_user(conn, username="admin", password_hash="hashed-value")

    fetched = get_by_username(conn, "admin")
    assert fetched.username == "admin"
    assert fetched.password_hash == "hashed-value"
    assert fetched.id == created.id


def test_new_admin_user_has_no_last_login_at(tmp_path):
    conn = _connection(tmp_path)
    create_admin_user(conn, username="admin", password_hash="hashed-value")

    fetched = get_by_username(conn, "admin")

    assert fetched.last_login_at is None


def test_get_by_username_returns_none_when_not_found(tmp_path):
    conn = _connection(tmp_path)

    assert get_by_username(conn, "nobody") is None


def test_record_login_sets_last_login_at(tmp_path):
    conn = _connection(tmp_path)
    create_admin_user(conn, username="admin", password_hash="hashed-value")

    record_login(conn, "admin")

    fetched = get_by_username(conn, "admin")
    assert fetched.last_login_at is not None


def test_update_password_changes_the_stored_hash(tmp_path):
    conn = _connection(tmp_path)
    create_admin_user(conn, username="admin", password_hash="old-hash")

    update_password(conn, "admin", "new-hash")

    fetched = get_by_username(conn, "admin")
    assert fetched.password_hash == "new-hash"
