from app.db import get_connection, run_migrations
from app.models.audit_log import write_audit_log
from app.services.login_lockout import LOGIN_FAILURE_LIMIT, is_locked_out


def _connection(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    run_migrations(conn)
    return conn


def test_not_locked_out_with_no_failures(tmp_path):
    conn = _connection(tmp_path)

    assert is_locked_out(conn, "admin") is False


def test_not_locked_out_below_the_limit(tmp_path):
    conn = _connection(tmp_path)
    for _ in range(LOGIN_FAILURE_LIMIT - 1):
        write_audit_log(conn, actor="admin", action="login.failure")

    assert is_locked_out(conn, "admin") is False


def test_locked_out_at_the_limit(tmp_path):
    conn = _connection(tmp_path)
    for _ in range(LOGIN_FAILURE_LIMIT):
        write_audit_log(conn, actor="admin", action="login.failure")

    assert is_locked_out(conn, "admin") is True


def test_a_different_username_is_not_affected(tmp_path):
    conn = _connection(tmp_path)
    for _ in range(LOGIN_FAILURE_LIMIT):
        write_audit_log(conn, actor="admin", action="login.failure")

    assert is_locked_out(conn, "someone-else") is False


def test_successful_logins_do_not_count_toward_lockout(tmp_path):
    conn = _connection(tmp_path)
    for _ in range(LOGIN_FAILURE_LIMIT):
        write_audit_log(conn, actor="admin", action="login.success")

    assert is_locked_out(conn, "admin") is False
