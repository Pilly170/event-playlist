import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

_SELECT_ENTRY = (
    "SELECT id, spotify_track_uri, source, request_id, inserted_position, "
    "snapshot_id_at_insert, added_at, played_at, removed_at FROM playlist_state"
)


@dataclass
class PlaylistStateEntry:
    id: int
    spotify_track_uri: str
    source: str
    request_id: int | None
    inserted_position: int | None
    snapshot_id_at_insert: str | None
    added_at: datetime
    played_at: datetime | None
    removed_at: datetime | None


def create_playlist_state_entry(
    conn: sqlite3.Connection,
    *,
    spotify_track_uri: str,
    source: str,
    request_id: int | None,
    inserted_position: int | None,
    snapshot_id_at_insert: str | None,
) -> PlaylistStateEntry:
    added_at = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute(
        """
        INSERT INTO playlist_state (
            spotify_track_uri, source, request_id, inserted_position,
            snapshot_id_at_insert, added_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            spotify_track_uri,
            source,
            request_id,
            inserted_position,
            snapshot_id_at_insert,
            added_at,
        ),
    )
    conn.commit()
    return get_by_id(conn, cursor.lastrowid)


def get_by_id(conn: sqlite3.Connection, entry_id: int) -> PlaylistStateEntry | None:
    row = conn.execute(f"{_SELECT_ENTRY} WHERE id = ?", (entry_id,)).fetchone()
    return _row_to_entry(row) if row else None


def get_by_request_id(
    conn: sqlite3.Connection, request_id: int
) -> PlaylistStateEntry | None:
    row = conn.execute(
        f"{_SELECT_ENTRY} WHERE request_id = ?", (request_id,)
    ).fetchone()
    return _row_to_entry(row) if row else None


def get_active_entry_for_uri(
    conn: sqlite3.Connection, spotify_track_uri: str
) -> PlaylistStateEntry | None:
    # Only ever matches source='request' rows — the cleanup worker must never treat
    # a pre-existing 'default' backbone track as something it's allowed to remove
    # (SPEC.md §6.4). ORDER BY added_at DESC picks the newest un-removed insertion,
    # so a track that was requested, played, removed, and requested again correctly
    # resolves to its latest entry rather than the stale removed one.
    row = conn.execute(
        f"{_SELECT_ENTRY} WHERE spotify_track_uri = ? AND source = 'request' AND removed_at IS NULL "
        "ORDER BY added_at DESC LIMIT 1",
        (spotify_track_uri,),
    ).fetchone()
    return _row_to_entry(row) if row else None


def mark_played(conn: sqlite3.Connection, entry_id: int) -> None:
    conn.execute(
        "UPDATE playlist_state SET played_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), entry_id),
    )
    conn.commit()


def mark_removed(conn: sqlite3.Connection, entry_id: int) -> None:
    conn.execute(
        "UPDATE playlist_state SET removed_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), entry_id),
    )
    conn.commit()


def _row_to_entry(row) -> PlaylistStateEntry:
    (
        id_,
        spotify_track_uri,
        source,
        request_id,
        inserted_position,
        snapshot_id_at_insert,
        added_at,
        played_at,
        removed_at,
    ) = row
    return PlaylistStateEntry(
        id=id_,
        spotify_track_uri=spotify_track_uri,
        source=source,
        request_id=request_id,
        inserted_position=inserted_position,
        snapshot_id_at_insert=snapshot_id_at_insert,
        added_at=datetime.fromisoformat(added_at),
        played_at=datetime.fromisoformat(played_at) if played_at else None,
        removed_at=datetime.fromisoformat(removed_at) if removed_at else None,
    )
