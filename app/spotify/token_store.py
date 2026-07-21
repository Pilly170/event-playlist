import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.services.crypto import TokenCipher


@dataclass
class StoredTokens:
    access_token: str
    refresh_token: str
    expires_at: datetime
    scope: str


def save_tokens(
    conn: sqlite3.Connection,
    cipher: TokenCipher,
    *,
    access_token: str,
    refresh_token: str,
    expires_in: int,
    scope: str,
) -> None:
    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    ).isoformat()
    conn.execute(
        """
        INSERT INTO spotify_auth (id, access_token_enc, refresh_token_enc, expires_at, scope)
        VALUES (1, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            access_token_enc = excluded.access_token_enc,
            refresh_token_enc = excluded.refresh_token_enc,
            expires_at = excluded.expires_at,
            scope = excluded.scope
        """,
        (
            cipher.encrypt(access_token),
            cipher.encrypt(refresh_token),
            expires_at,
            scope,
        ),
    )
    conn.commit()


def load_tokens(conn: sqlite3.Connection, cipher: TokenCipher) -> StoredTokens | None:
    row = conn.execute(
        "SELECT access_token_enc, refresh_token_enc, expires_at, scope FROM spotify_auth WHERE id = 1"
    ).fetchone()
    if row is None:
        return None

    access_token_enc, refresh_token_enc, expires_at, scope = row
    return StoredTokens(
        access_token=cipher.decrypt(access_token_enc),
        refresh_token=cipher.decrypt(refresh_token_enc),
        expires_at=datetime.fromisoformat(expires_at),
        scope=scope,
    )
