from datetime import datetime, timedelta, timezone

from app.db import get_connection, run_migrations
from app.models.audit_log import count_recent_actions, write_audit_log


def _connection(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    run_migrations(conn)
    return conn


def test_count_recent_actions_counts_matching_actor_and_action(tmp_path):
    conn = _connection(tmp_path)
    write_audit_log(conn, actor="admin", action="login.failure")
    write_audit_log(conn, actor="admin", action="login.failure")
    write_audit_log(conn, actor="admin", action="login.success")

    since = datetime.now(timezone.utc) - timedelta(minutes=15)
    count = count_recent_actions(
        conn, actor="admin", action="login.failure", since=since
    )

    assert count == 2


def test_count_recent_actions_ignores_a_different_actor(tmp_path):
    conn = _connection(tmp_path)
    write_audit_log(conn, actor="someone-else", action="login.failure")

    since = datetime.now(timezone.utc) - timedelta(minutes=15)
    count = count_recent_actions(
        conn, actor="admin", action="login.failure", since=since
    )

    assert count == 0


def test_count_recent_actions_excludes_entries_before_the_window(tmp_path):
    conn = _connection(tmp_path)
    write_audit_log(conn, actor="admin", action="login.failure")

    since = datetime.now(timezone.utc) + timedelta(minutes=1)  # nothing is this recent
    count = count_recent_actions(
        conn, actor="admin", action="login.failure", since=since
    )

    assert count == 0
