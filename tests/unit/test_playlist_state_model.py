from app.db import get_connection, run_migrations
from app.models.playlist_state import create_playlist_state_entry, get_by_request_id


def _connection(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    run_migrations(conn)
    return conn


def test_create_playlist_state_entry_persists_fields(tmp_path):
    conn = _connection(tmp_path)

    entry = create_playlist_state_entry(
        conn,
        spotify_track_uri="spotify:track:abc123",
        source="request",
        request_id=42,
        inserted_position=7,
        snapshot_id_at_insert="snap-1",
    )

    assert entry.spotify_track_uri == "spotify:track:abc123"
    assert entry.source == "request"
    assert entry.request_id == 42
    assert entry.inserted_position == 7
    assert entry.snapshot_id_at_insert == "snap-1"
    assert entry.played_at is None
    assert entry.removed_at is None


def test_get_by_request_id_round_trips(tmp_path):
    conn = _connection(tmp_path)
    created = create_playlist_state_entry(
        conn,
        spotify_track_uri="spotify:track:abc123",
        source="request",
        request_id=42,
        inserted_position=7,
        snapshot_id_at_insert="snap-1",
    )

    fetched = get_by_request_id(conn, 42)

    assert fetched.id == created.id


def test_get_by_request_id_returns_none_when_not_found(tmp_path):
    conn = _connection(tmp_path)

    assert get_by_request_id(conn, 999) is None
