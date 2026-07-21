import logging

from app.db import get_connection, run_migrations
from app.models.admin_users import count_admin_users, get_by_username
from app.security.auth import verify_password
from app.services.admin_seed import (
    clear_initial_admin_password_file,
    initial_admin_password_path,
    seed_default_admin_if_needed,
)


def _connection(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    run_migrations(conn)
    return conn, db_path


def test_seeds_a_single_admin_user_when_none_exist(tmp_path):
    conn, db_path = _connection(tmp_path)

    seed_default_admin_if_needed(conn, database_path=db_path)

    assert count_admin_users(conn) == 1


def test_does_nothing_when_an_admin_already_exists(tmp_path):
    conn, db_path = _connection(tmp_path)
    seed_default_admin_if_needed(conn, database_path=db_path)
    initial_admin_password_path(db_path).unlink()

    seed_default_admin_if_needed(conn, database_path=db_path)

    assert count_admin_users(conn) == 1
    assert not initial_admin_password_path(db_path).exists()


def test_writes_a_password_file_that_verifies_against_the_seeded_hash(tmp_path):
    conn, db_path = _connection(tmp_path)

    seed_default_admin_if_needed(conn, database_path=db_path)

    password_file = initial_admin_password_path(db_path)
    assert password_file.exists()
    seed_password = password_file.read_text().strip()
    user = get_by_username(conn, "admin")
    assert verify_password(seed_password, user.password_hash)


def test_password_file_is_only_readable_by_the_owner(tmp_path):
    conn, db_path = _connection(tmp_path)

    seed_default_admin_if_needed(conn, database_path=db_path)

    mode = initial_admin_password_path(db_path).stat().st_mode
    assert oct(mode)[-3:] == "600"


def test_does_not_log_the_raw_password(tmp_path, caplog):
    conn, db_path = _connection(tmp_path)

    with caplog.at_level(logging.WARNING):
        seed_default_admin_if_needed(conn, database_path=db_path)

    seed_password = initial_admin_password_path(db_path).read_text().strip()
    logged_text = "\n".join(record.getMessage() for record in caplog.records)
    assert seed_password not in logged_text


def test_clear_initial_admin_password_file_removes_it(tmp_path):
    conn, db_path = _connection(tmp_path)
    seed_default_admin_if_needed(conn, database_path=db_path)
    assert initial_admin_password_path(db_path).exists()

    clear_initial_admin_password_file(db_path)

    assert not initial_admin_password_path(db_path).exists()


def test_clear_initial_admin_password_file_is_a_noop_when_missing(tmp_path):
    db_path = str(tmp_path / "test.db")

    clear_initial_admin_password_file(db_path)  # must not raise
