import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class AuditLogEntry:
    id: int
    at: datetime
    actor: str
    action: str
    detail: str | None


def write_audit_log(
    conn: sqlite3.Connection, *, actor: str, action: str, detail: str | None = None
) -> None:
    conn.execute(
        "INSERT INTO audit_log (at, actor, action, detail) VALUES (?, ?, ?, ?)",
        (datetime.now(timezone.utc).isoformat(), actor, action, detail),
    )
    conn.commit()


def count_recent_actions(
    conn: sqlite3.Connection, *, actor: str, action: str, since: datetime
) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE actor = ? AND action = ? AND at >= ?",
        (actor, action, since.isoformat()),
    ).fetchone()[0]


def list_audit_log(conn: sqlite3.Connection) -> list[AuditLogEntry]:
    rows = conn.execute(
        "SELECT id, at, actor, action, detail FROM audit_log ORDER BY id DESC"
    ).fetchall()
    return [
        AuditLogEntry(
            id=id_,
            at=datetime.fromisoformat(at),
            actor=actor,
            action=action,
            detail=detail,
        )
        for id_, at, actor, action, detail in rows
    ]
