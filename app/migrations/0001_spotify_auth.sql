-- Encrypted Spotify OAuth tokens for the venue account (SPEC.md §4)
CREATE TABLE spotify_auth (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    access_token_enc BLOB NOT NULL,
    refresh_token_enc BLOB NOT NULL,
    expires_at TEXT NOT NULL,
    scope TEXT NOT NULL
);
