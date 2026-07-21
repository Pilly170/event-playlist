import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from app.services.reference_code import generate_reference_code

_MAX_REFERENCE_CODE_ATTEMPTS = 5

_SELECT_REQUEST = (
    "SELECT id, spotify_track_uri, track_name, artist_name, is_explicit, requestor_name, "
    "reference_code, device_token, client_ip, status, requested_at, decided_at, decided_by, "
    "playlist_insert_position FROM requests"
)


class DuplicateActiveRequestError(Exception):
    def __init__(self, spotify_track_uri: str):
        super().__init__(f"An active request already exists for {spotify_track_uri}")
        self.spotify_track_uri = spotify_track_uri


@dataclass
class Request:
    id: int
    spotify_track_uri: str
    track_name: str
    artist_name: str
    is_explicit: bool
    requestor_name: str
    reference_code: str
    device_token: str
    client_ip: str
    status: str
    requested_at: datetime
    decided_at: datetime | None
    decided_by: str | None
    playlist_insert_position: int | None


def has_pending_duplicate(conn: sqlite3.Connection, spotify_track_uri: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM requests WHERE spotify_track_uri = ? AND status = 'pending' LIMIT 1",
        (spotify_track_uri,),
    ).fetchone()
    return row is not None


def create_request(
    conn: sqlite3.Connection,
    *,
    spotify_track_uri: str,
    track_name: str,
    artist_name: str,
    is_explicit: bool,
    requestor_name: str,
    device_token: str,
    client_ip: str,
) -> Request:
    if has_pending_duplicate(conn, spotify_track_uri):
        raise DuplicateActiveRequestError(spotify_track_uri)

    requested_at = datetime.now(timezone.utc).isoformat()

    for _ in range(_MAX_REFERENCE_CODE_ATTEMPTS):
        reference_code = generate_reference_code()
        try:
            cursor = conn.execute(
                """
                INSERT INTO requests (
                    spotify_track_uri, track_name, artist_name, is_explicit,
                    requestor_name, reference_code, device_token, client_ip,
                    status, requested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    spotify_track_uri,
                    track_name,
                    artist_name,
                    int(is_explicit),
                    requestor_name,
                    reference_code,
                    device_token,
                    client_ip,
                    requested_at,
                ),
            )
        except sqlite3.IntegrityError:
            conn.rollback()
            # Either a reference_code collision (retry with a new one) or a
            # duplicate submitted in the race window since the check above —
            # tell them apart by re-checking, rather than parsing sqlite's
            # error text for which constraint fired.
            if has_pending_duplicate(conn, spotify_track_uri):
                raise DuplicateActiveRequestError(spotify_track_uri) from None
            continue
        else:
            conn.commit()
            return get_by_id(conn, cursor.lastrowid)

    raise RuntimeError(
        "Could not generate a unique reference code after several attempts"
    )


def get_by_id(conn: sqlite3.Connection, request_id: int) -> Request | None:
    row = conn.execute(f"{_SELECT_REQUEST} WHERE id = ?", (request_id,)).fetchone()
    return _row_to_request(row) if row else None


def get_by_reference_code(
    conn: sqlite3.Connection, reference_code: str
) -> Request | None:
    row = conn.execute(
        f"{_SELECT_REQUEST} WHERE reference_code = ?", (reference_code,)
    ).fetchone()
    return _row_to_request(row) if row else None


def count_recent_requests_by_device_token(
    conn: sqlite3.Connection, device_token: str, since: datetime
) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM requests WHERE device_token = ? AND requested_at >= ?",
        (device_token, since.isoformat()),
    ).fetchone()[0]


def count_recent_requests_by_client_ip(
    conn: sqlite3.Connection, client_ip: str, since: datetime
) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM requests WHERE client_ip = ? AND requested_at >= ?",
        (client_ip, since.isoformat()),
    ).fetchone()[0]


def mark_request_added(
    conn: sqlite3.Connection,
    *,
    request_id: int,
    decided_by: str,
    playlist_insert_position: int,
) -> None:
    conn.execute(
        """
        UPDATE requests
        SET status = 'added', decided_at = ?, decided_by = ?, playlist_insert_position = ?
        WHERE id = ?
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            decided_by,
            playlist_insert_position,
            request_id,
        ),
    )
    conn.commit()


def delete_request(conn: sqlite3.Connection, request_id: int) -> None:
    conn.execute("DELETE FROM requests WHERE id = ?", (request_id,))
    conn.commit()


def list_pending(conn: sqlite3.Connection) -> list[Request]:
    rows = conn.execute(
        f"{_SELECT_REQUEST} WHERE status = 'pending' ORDER BY requested_at"
    ).fetchall()
    return [_row_to_request(row) for row in rows]


def list_added(conn: sqlite3.Connection) -> list[Request]:
    rows = conn.execute(
        f"{_SELECT_REQUEST} WHERE status = 'added' ORDER BY decided_at DESC"
    ).fetchall()
    return [_row_to_request(row) for row in rows]


def _row_to_request(row) -> Request:
    (
        id_,
        spotify_track_uri,
        track_name,
        artist_name,
        is_explicit,
        requestor_name,
        reference_code,
        device_token,
        client_ip,
        status,
        requested_at,
        decided_at,
        decided_by,
        playlist_insert_position,
    ) = row
    return Request(
        id=id_,
        spotify_track_uri=spotify_track_uri,
        track_name=track_name,
        artist_name=artist_name,
        is_explicit=bool(is_explicit),
        requestor_name=requestor_name,
        reference_code=reference_code,
        device_token=device_token,
        client_ip=client_ip,
        status=status,
        requested_at=datetime.fromisoformat(requested_at),
        decided_at=datetime.fromisoformat(decided_at) if decided_at else None,
        decided_by=decided_by,
        playlist_insert_position=playlist_insert_position,
    )
