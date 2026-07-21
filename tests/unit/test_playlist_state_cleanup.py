from app.db import get_connection, run_migrations
from app.models.playlist_state import (
    create_playlist_state_entry,
    get_active_entry_for_uri,
    get_by_id,
    mark_played,
    mark_removed,
)


def _connection(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    run_migrations(conn)
    return conn


def _create(conn, **overrides):
    defaults = dict(
        spotify_track_uri="spotify:track:abc123",
        source="request",
        request_id=1,
        inserted_position=5,
        snapshot_id_at_insert="snap-1",
    )
    defaults.update(overrides)
    return create_playlist_state_entry(conn, **defaults)


def test_get_active_entry_for_uri_finds_a_request_sourced_unremoved_entry(tmp_path):
    conn = _connection(tmp_path)
    created = _create(conn)

    found = get_active_entry_for_uri(conn, "spotify:track:abc123")

    assert found.id == created.id


def test_get_active_entry_for_uri_returns_none_when_no_match(tmp_path):
    conn = _connection(tmp_path)

    assert get_active_entry_for_uri(conn, "spotify:track:nope") is None


def test_get_active_entry_for_uri_ignores_default_sourced_entries(tmp_path):
    conn = _connection(tmp_path)
    _create(conn, source="default", request_id=None)

    assert get_active_entry_for_uri(conn, "spotify:track:abc123") is None


def test_get_active_entry_for_uri_ignores_already_removed_entries(tmp_path):
    conn = _connection(tmp_path)
    created = _create(conn)
    mark_removed(conn, created.id)

    assert get_active_entry_for_uri(conn, "spotify:track:abc123") is None


def test_get_active_entry_for_uri_finds_the_newest_reinsertion_after_a_prior_removal(
    tmp_path,
):
    conn = _connection(tmp_path)
    first = _create(conn, request_id=1)
    mark_removed(conn, first.id)
    second = _create(conn, request_id=2)

    found = get_active_entry_for_uri(conn, "spotify:track:abc123")

    assert found.id == second.id


def test_mark_played_sets_played_at(tmp_path):
    conn = _connection(tmp_path)
    created = _create(conn)

    mark_played(conn, created.id)

    assert get_by_id(conn, created.id).played_at is not None


def test_mark_removed_sets_removed_at(tmp_path):
    conn = _connection(tmp_path)
    created = _create(conn)

    mark_removed(conn, created.id)

    assert get_by_id(conn, created.id).removed_at is not None
