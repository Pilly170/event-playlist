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
