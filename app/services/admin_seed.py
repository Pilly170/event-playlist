import logging
import secrets
import sqlite3
from pathlib import Path

from app.models.admin_users import count_admin_users, create_admin_user
from app.models.audit_log import write_audit_log
from app.security.auth import hash_password

logger = logging.getLogger(__name__)


def initial_admin_password_path(database_path: str) -> Path:
    return Path(database_path).parent / "initial_admin_password.txt"


def seed_default_admin_if_needed(
    conn: sqlite3.Connection, *, database_path: str
) -> None:
    if count_admin_users(conn) > 0:
        return

    seed_password = secrets.token_urlsafe(16)
    create_admin_user(
        conn, username="admin", password_hash=hash_password(seed_password)
    )
    write_audit_log(
        conn, actor="system", action="admin.seeded", detail="username=admin"
    )

    # The password itself must never reach the logger — logs are commonly shipped
    # to longer-retention, less access-controlled storage than the app's own data
    # volume. Write it to a file there instead, and log only its path.
    password_path = initial_admin_password_path(database_path)
    password_path.write_text(seed_password + "\n")
    password_path.chmod(0o600)

    logger.warning(
        "Seeded default admin user 'admin'. One-time password written to %s — "
        "you will be required to change it on first login, after which this "
        "file is deleted automatically.",
        password_path,
    )


def clear_initial_admin_password_file(database_path: str) -> None:
    initial_admin_password_path(database_path).unlink(missing_ok=True)
