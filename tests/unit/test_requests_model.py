from datetime import datetime, timedelta, timezone

import pytest

from app.db import get_connection, run_migrations
from app.models.requests import (
    DuplicateActiveRequestError,
    count_recent_requests_by_client_ip,
    count_recent_requests_by_device_token,
    create_request,
    get_by_id,
    get_by_reference_code,
    has_pending_duplicate,
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


def test_create_request_defaults_to_pending_status(tmp_path):
    conn = _connection(tmp_path)

    request = _create(conn)

    assert request.status == "pending"


def test_create_request_generates_a_reference_code(tmp_path):
    conn = _connection(tmp_path)

    request = _create(conn)

    assert len(request.reference_code) == 6


def test_get_by_id_round_trips(tmp_path):
    conn = _connection(tmp_path)
    created = _create(conn)

    fetched = get_by_id(conn, created.id)

    assert fetched.spotify_track_uri == "spotify:track:abc123"
    assert fetched.requestor_name == "Alex"


def test_get_by_reference_code_round_trips(tmp_path):
    conn = _connection(tmp_path)
    created = _create(conn)

    fetched = get_by_reference_code(conn, created.reference_code)

    assert fetched.id == created.id


def test_get_by_reference_code_returns_none_when_not_found(tmp_path):
    conn = _connection(tmp_path)

    assert get_by_reference_code(conn, "ZZZZZZ") is None


def test_has_pending_duplicate_is_false_when_no_requests_exist(tmp_path):
    conn = _connection(tmp_path)

    assert has_pending_duplicate(conn, "spotify:track:abc123") is False


def test_has_pending_duplicate_is_true_after_a_pending_request_exists(tmp_path):
    conn = _connection(tmp_path)
    _create(conn, spotify_track_uri="spotify:track:xyz789")

    assert has_pending_duplicate(conn, "spotify:track:xyz789") is True


def test_create_request_raises_on_duplicate_pending_track(tmp_path):
    conn = _connection(tmp_path)
    _create(conn, spotify_track_uri="spotify:track:dupe")

    with pytest.raises(DuplicateActiveRequestError):
        _create(
            conn, spotify_track_uri="spotify:track:dupe", requestor_name="Someone Else"
        )


def test_count_recent_requests_by_device_token(tmp_path):
    conn = _connection(tmp_path)
    _create(conn, device_token="device-a")
    _create(conn, device_token="device-a", spotify_track_uri="spotify:track:other")
    _create(conn, device_token="device-b", spotify_track_uri="spotify:track:third")

    since = datetime.now(timezone.utc) - timedelta(minutes=30)
    assert count_recent_requests_by_device_token(conn, "device-a", since) == 2
    assert count_recent_requests_by_device_token(conn, "device-b", since) == 1


def test_count_recent_requests_by_device_token_excludes_old_requests(tmp_path):
    conn = _connection(tmp_path)
    _create(conn, device_token="device-a")

    since = datetime.now(timezone.utc) + timedelta(minutes=1)  # nothing is this recent
    assert count_recent_requests_by_device_token(conn, "device-a", since) == 0


def test_count_recent_requests_by_client_ip(tmp_path):
    conn = _connection(tmp_path)
    _create(conn, client_ip="9.9.9.9")
    _create(
        conn,
        client_ip="9.9.9.9",
        spotify_track_uri="spotify:track:other",
        device_token="device-2",
    )

    since = datetime.now(timezone.utc) - timedelta(minutes=30)
    assert count_recent_requests_by_client_ip(conn, "9.9.9.9", since) == 2
