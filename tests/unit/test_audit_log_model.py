from app.db import get_connection, run_migrations
from app.models.audit_log import list_audit_log, write_audit_log


def _connection(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    run_migrations(conn)
    return conn


def test_write_audit_log_records_actor_action_and_detail(tmp_path):
    conn = _connection(tmp_path)

    write_audit_log(
        conn,
        actor="admin",
        action="config.update",
        detail="poll_interval_seconds: 15 -> 20",
    )

    entries = list_audit_log(conn)
    assert len(entries) == 1
    assert entries[0].actor == "admin"
    assert entries[0].action == "config.update"
    assert entries[0].detail == "poll_interval_seconds: 15 -> 20"


def test_write_audit_log_detail_is_optional(tmp_path):
    conn = _connection(tmp_path)

    write_audit_log(conn, actor="system", action="worker.cleanup")

    entries = list_audit_log(conn)
    assert entries[0].detail is None


def test_list_audit_log_returns_most_recent_first(tmp_path):
    conn = _connection(tmp_path)
    write_audit_log(conn, actor="admin", action="login.success")
    write_audit_log(conn, actor="admin", action="config.update")

    entries = list_audit_log(conn)

    assert [e.action for e in entries] == ["config.update", "login.success"]
