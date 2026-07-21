import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

_EDITABLE_FIELDS = {
    "require_admin_approval",
    "exclude_explicit",
    "default_playlist_id",
    "insert_tracks_ahead",
    "playlist_repeat_enabled",
    "poll_interval_seconds",
}

_SELECT_CONFIG = (
    "SELECT require_admin_approval, exclude_explicit, default_playlist_id, "
    "insert_tracks_ahead, playlist_repeat_enabled, poll_interval_seconds, updated_at "
    "FROM config WHERE id = 1"
)


@dataclass
class AppConfig:
    require_admin_approval: bool
    exclude_explicit: bool
    default_playlist_id: str | None
    insert_tracks_ahead: int
    playlist_repeat_enabled: bool
    poll_interval_seconds: int
    updated_at: datetime


def get_config(conn: sqlite3.Connection) -> AppConfig:
    row = conn.execute(_SELECT_CONFIG).fetchone()
    return _row_to_config(row)


def update_config(conn: sqlite3.Connection, **changes) -> AppConfig:
    unknown_fields = set(changes) - _EDITABLE_FIELDS
    if unknown_fields:
        raise ValueError(f"Unknown config fields: {sorted(unknown_fields)}")

    if changes:
        # Field names are interpolated, but only after the allowlist check above —
        # every key in `changes` is guaranteed to be one of _EDITABLE_FIELDS, never
        # arbitrary input. Values are still passed as parameters, never interpolated.
        set_clause = ", ".join(f"{field} = ?" for field in changes)
        conn.execute(
            f"UPDATE config SET {set_clause}, updated_at = ?"  # nosec B608
            " WHERE id = 1",
            (*changes.values(), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()

    return get_config(conn)


def _row_to_config(row) -> AppConfig:
    (
        require_admin_approval,
        exclude_explicit,
        default_playlist_id,
        insert_tracks_ahead,
        playlist_repeat_enabled,
        poll_interval_seconds,
        updated_at,
    ) = row
    return AppConfig(
        require_admin_approval=bool(require_admin_approval),
        exclude_explicit=bool(exclude_explicit),
        default_playlist_id=default_playlist_id,
        insert_tracks_ahead=insert_tracks_ahead,
        playlist_repeat_enabled=bool(playlist_repeat_enabled),
        poll_interval_seconds=poll_interval_seconds,
        updated_at=datetime.fromisoformat(updated_at),
    )
