import pytest

from app.db import get_connection, run_migrations
from app.models.config import get_config, update_config


def _connection(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    run_migrations(conn)
    return conn


def test_get_config_returns_the_seeded_defaults(tmp_path):
    conn = _connection(tmp_path)

    config = get_config(conn)

    assert config.require_admin_approval is True
    assert config.exclude_explicit is True
    assert config.default_playlist_id is None
    assert config.insert_tracks_ahead == 3
    assert config.playlist_repeat_enabled is True
    assert config.poll_interval_seconds == 15


def test_update_config_changes_only_the_given_fields(tmp_path):
    conn = _connection(tmp_path)

    update_config(conn, insert_tracks_ahead=5)

    config = get_config(conn)
    assert config.insert_tracks_ahead == 5
    assert config.exclude_explicit is True


def test_update_config_bumps_updated_at(tmp_path):
    conn = _connection(tmp_path)
    before = get_config(conn).updated_at

    updated = update_config(conn, poll_interval_seconds=30)

    assert updated.updated_at >= before


def test_update_config_rejects_unknown_field(tmp_path):
    conn = _connection(tmp_path)

    with pytest.raises(ValueError):
        update_config(conn, not_a_real_field=True)
