from app.db import get_connection, run_migrations
from app.models.requests import create_request
from app.services.rate_limit import (
    DEVICE_TOKEN_LIMIT,
    IP_LIMIT,
    check_rate_limit,
)


def _connection(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    run_migrations(conn)
    return conn


def _create(conn, i, **overrides):
    defaults = dict(
        spotify_track_uri=f"spotify:track:{i}",
        track_name="A Song",
        artist_name="An Artist",
        is_explicit=False,
        requestor_name="Alex",
        device_token="device-1",
        client_ip="1.2.3.4",
    )
    defaults.update(overrides)
    create_request(conn, **defaults)


def test_allows_when_under_both_limits(tmp_path):
    conn = _connection(tmp_path)

    result = check_rate_limit(conn, device_token="device-1", client_ip="1.2.3.4")

    assert result.allowed is True


def test_blocks_once_device_token_limit_is_reached(tmp_path):
    conn = _connection(tmp_path)
    for i in range(DEVICE_TOKEN_LIMIT):
        _create(conn, i, device_token="device-1", client_ip=f"9.9.9.{i}")

    result = check_rate_limit(conn, device_token="device-1", client_ip="8.8.8.8")

    assert result.allowed is False
    assert result.reason == "device_token"


def test_does_not_block_a_different_device_token(tmp_path):
    conn = _connection(tmp_path)
    for i in range(DEVICE_TOKEN_LIMIT):
        _create(conn, i, device_token="device-1", client_ip=f"9.9.9.{i}")

    result = check_rate_limit(conn, device_token="device-2", client_ip="9.9.9.0")

    assert result.allowed is True


def test_blocks_once_ip_backstop_is_reached_even_with_distinct_device_tokens(tmp_path):
    conn = _connection(tmp_path)
    for i in range(IP_LIMIT):
        _create(conn, i, device_token=f"device-{i}", client_ip="1.2.3.4")

    result = check_rate_limit(
        conn, device_token="brand-new-device", client_ip="1.2.3.4"
    )

    assert result.allowed is False
    assert result.reason == "client_ip"
