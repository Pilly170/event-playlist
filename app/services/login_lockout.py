import sqlite3
from datetime import datetime, timedelta, timezone

from app.models.audit_log import count_recent_actions

# SPEC.md §6.3: "lockout after 5 failed attempts within 15 minutes." Reuses the
# existing audit_log (already records login.failure on every bad attempt) instead
# of a new table — the lockout is self-expiring for free, since failures fall out
# of the window on their own rather than needing a separate "locked until" timestamp.
LOGIN_FAILURE_LIMIT = 5
LOGIN_FAILURE_WINDOW_MINUTES = 15


def is_locked_out(conn: sqlite3.Connection, username: str) -> bool:
    since = datetime.now(timezone.utc) - timedelta(minutes=LOGIN_FAILURE_WINDOW_MINUTES)
    recent_failures = count_recent_actions(
        conn, actor=username, action="login.failure", since=since
    )
    return recent_failures >= LOGIN_FAILURE_LIMIT
