from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet

from app.db import get_connection, run_migrations
from app.services.crypto import TokenCipher
from app.spotify.token_store import load_tokens, save_tokens


def _connection(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    run_migrations(conn)
    return conn


def test_load_tokens_returns_none_when_never_connected(tmp_path):
    conn = _connection(tmp_path)
    cipher = TokenCipher(key=Fernet.generate_key().decode())

    assert load_tokens(conn, cipher) is None


def test_save_then_load_round_trips_tokens(tmp_path):
    conn = _connection(tmp_path)
    cipher = TokenCipher(key=Fernet.generate_key().decode())

    save_tokens(
        conn,
        cipher,
        access_token="access-1",
        refresh_token="refresh-1",
        expires_in=3600,
        scope="playlist-read-private",
    )

    stored = load_tokens(conn, cipher)

    assert stored.access_token == "access-1"
    assert stored.refresh_token == "refresh-1"
    assert stored.scope == "playlist-read-private"
    expected_expiry = datetime.now(timezone.utc) + timedelta(seconds=3600)
    assert abs((stored.expires_at - expected_expiry).total_seconds()) < 5


def test_tokens_are_encrypted_at_rest(tmp_path):
    conn = _connection(tmp_path)
    cipher = TokenCipher(key=Fernet.generate_key().decode())

    save_tokens(
        conn,
        cipher,
        access_token="super-secret-access-token",
        refresh_token="super-secret-refresh-token",
        expires_in=3600,
        scope="playlist-read-private",
    )

    row = conn.execute(
        "SELECT access_token_enc, refresh_token_enc FROM spotify_auth WHERE id = 1"
    ).fetchone()
    assert b"super-secret-access-token" not in row[0]
    assert b"super-secret-refresh-token" not in row[1]


def test_save_tokens_overwrites_previous_connection(tmp_path):
    conn = _connection(tmp_path)
    cipher = TokenCipher(key=Fernet.generate_key().decode())
    save_tokens(
        conn,
        cipher,
        access_token="access-1",
        refresh_token="refresh-1",
        expires_in=3600,
        scope="scope-a",
    )

    save_tokens(
        conn,
        cipher,
        access_token="access-2",
        refresh_token="refresh-2",
        expires_in=3600,
        scope="scope-b",
    )

    stored = load_tokens(conn, cipher)
    assert stored.access_token == "access-2"
    assert stored.refresh_token == "refresh-2"
    row_count = conn.execute("SELECT COUNT(*) FROM spotify_auth").fetchone()[0]
    assert row_count == 1
