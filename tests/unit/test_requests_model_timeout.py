from datetime import datetime, timedelta, timezone

from app.db import get_connection, run_migrations
from app.models.requests import (
    create_request,
    get_by_id,
    list_pending_needing_reminder,
    list_pending_requested_before,
    mark_reminder_sent,
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


def _backdate(conn, request_id, *, minutes_ago):
    requested_at = (
        datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    ).isoformat()
    conn.execute(
        "UPDATE requests SET requested_at = ? WHERE id = ?",
        (requested_at, request_id),
    )
    conn.commit()


def test_new_request_has_no_reminder_sent_at(tmp_path):
    conn = _connection(tmp_path)
    created = _create(conn)

    assert created.reminder_sent_at is None


def test_list_pending_requested_before_returns_only_older_pending_requests(tmp_path):
    conn = _connection(tmp_path)
    old = _create(conn, spotify_track_uri="spotify:track:old")
    _backdate(conn, old.id, minutes_ago=20)
    _create(conn, spotify_track_uri="spotify:track:new")

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
    results = list_pending_requested_before(conn, cutoff)

    assert [r.id for r in results] == [old.id]


def test_list_pending_needing_reminder_excludes_already_reminded(tmp_path):
    conn = _connection(tmp_path)
    old = _create(conn, spotify_track_uri="spotify:track:old")
    _backdate(conn, old.id, minutes_ago=20)
    already_reminded = _create(conn, spotify_track_uri="spotify:track:reminded")
    _backdate(conn, already_reminded.id, minutes_ago=20)
    mark_reminder_sent(conn, already_reminded.id)

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
    results = list_pending_needing_reminder(conn, cutoff)

    assert [r.id for r in results] == [old.id]


def test_mark_reminder_sent_sets_the_timestamp(tmp_path):
    conn = _connection(tmp_path)
    created = _create(conn)

    mark_reminder_sent(conn, created.id)

    updated = get_by_id(conn, created.id)
    assert updated.reminder_sent_at is not None
