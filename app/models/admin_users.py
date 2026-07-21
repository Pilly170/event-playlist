import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class AdminUser:
    id: int
    username: str
    password_hash: str
    created_at: datetime
    last_login_at: datetime | None


def count_admin_users(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM admin_users").fetchone()[0]


def create_admin_user(
    conn: sqlite3.Connection, *, username: str, password_hash: str
) -> AdminUser:
    created_at = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute(
        "INSERT INTO admin_users (username, password_hash, created_at, last_login_at) VALUES (?, ?, ?, NULL)",
        (username, password_hash, created_at),
    )
    conn.commit()
    return AdminUser(
        id=cursor.lastrowid,
        username=username,
        password_hash=password_hash,
        created_at=datetime.fromisoformat(created_at),
        last_login_at=None,
    )


def get_by_username(conn: sqlite3.Connection, username: str) -> AdminUser | None:
    row = conn.execute(
        "SELECT id, username, password_hash, created_at, last_login_at FROM admin_users WHERE username = ?",
        (username,),
    ).fetchone()
    if row is None:
        return None
    id_, username, password_hash, created_at, last_login_at = row
    return AdminUser(
        id=id_,
        username=username,
        password_hash=password_hash,
        created_at=datetime.fromisoformat(created_at),
        last_login_at=datetime.fromisoformat(last_login_at) if last_login_at else None,
    )


def update_password(
    conn: sqlite3.Connection, username: str, password_hash: str
) -> None:
    conn.execute(
        "UPDATE admin_users SET password_hash = ? WHERE username = ?",
        (password_hash, username),
    )
    conn.commit()


def record_login(conn: sqlite3.Connection, username: str) -> None:
    conn.execute(
        "UPDATE admin_users SET last_login_at = ? WHERE username = ?",
        (datetime.now(timezone.utc).isoformat(), username),
    )
    conn.commit()
