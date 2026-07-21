from app.db import get_connection, run_migrations


def test_run_migrations_creates_spotify_auth_table(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))

    run_migrations(conn)

    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "spotify_auth" in tables


def test_run_migrations_does_not_reapply_already_applied_migrations(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    run_migrations(conn)

    run_migrations(conn)

    applied_count = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    assert applied_count == 1
