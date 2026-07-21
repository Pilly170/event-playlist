from app.db import get_connection, run_migrations
from app.models.requests import (
    create_request,
    delete_request,
    get_by_id,
    list_added,
    list_pending,
    mark_request_added,
)


def _connection(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    run_migrations(conn)
    return conn


def _create(conn, **overrides):
    defaults = dict(
        spotify_track_uri="spotify:track:abc123",
        track_name="A Song",
        artist_name="An Artist",
        is_explicit=False,
        requestor_name="Alex",
        device_token="device-1",
        client_ip="1.2.3.4",
    )
    defaults.update(overrides)
    return create_request(conn, **defaults)


def test_mark_request_added_updates_status_and_decision_fields(tmp_path):
    conn = _connection(tmp_path)
    created = _create(conn)

    mark_request_added(
        conn, request_id=created.id, decided_by="admin", playlist_insert_position=5
    )

    updated = get_by_id(conn, created.id)
    assert updated.status == "added"
    assert updated.decided_by == "admin"
    assert updated.decided_at is not None
    assert updated.playlist_insert_position == 5


def test_delete_request_removes_the_row(tmp_path):
    conn = _connection(tmp_path)
    created = _create(conn)

    delete_request(conn, created.id)

    assert get_by_id(conn, created.id) is None


def test_list_pending_returns_only_pending_requests(tmp_path):
    conn = _connection(tmp_path)
    pending = _create(conn, spotify_track_uri="spotify:track:pending1")
    added = _create(conn, spotify_track_uri="spotify:track:added1")
    mark_request_added(
        conn, request_id=added.id, decided_by="admin", playlist_insert_position=1
    )

    result = list_pending(conn)

    assert [r.id for r in result] == [pending.id]


def test_list_added_returns_only_added_requests(tmp_path):
    conn = _connection(tmp_path)
    _create(conn, spotify_track_uri="spotify:track:pending1")
    added = _create(conn, spotify_track_uri="spotify:track:added1")
    mark_request_added(
        conn, request_id=added.id, decided_by="admin", playlist_insert_position=1
    )

    result = list_added(conn)

    assert [r.id for r in result] == [added.id]


def test_new_request_has_no_decision_fields_yet(tmp_path):
    conn = _connection(tmp_path)
    created = _create(conn)

    assert created.decided_at is None
    assert created.decided_by is None
    assert created.playlist_insert_position is None
