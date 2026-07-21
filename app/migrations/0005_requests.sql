-- Song requests (SPEC.md §4). `reference_code` is not in the SPEC.md §4 schema
-- listing verbatim, but §6.1/§6.2 explicitly require generating one at submission
-- and using it (not name-matching) as the public status-lookup key, so it needs
-- its own persisted, unique column.
CREATE TABLE requests (
    id INTEGER PRIMARY KEY,
    spotify_track_uri TEXT NOT NULL,
    track_name TEXT NOT NULL,
    artist_name TEXT NOT NULL,
    is_explicit INTEGER NOT NULL DEFAULT 0,
    requestor_name TEXT NOT NULL,
    reference_code TEXT UNIQUE NOT NULL,
    device_token TEXT NOT NULL,               -- opaque per-browser cookie value; primary rate-limit key (§6.1) — not an identity, no PII
    client_ip TEXT NOT NULL,                  -- coarse abuse backstop only (§6.1), not the primary rate-limit key
    status TEXT NOT NULL DEFAULT 'pending',   -- pending | added
    requested_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT,                          -- admin_users.username
    playlist_insert_position INTEGER
);

-- DB-level backstop against duplicate pending requests for the same track,
-- on top of the application-level checks in §6.1/§6.3 (closes the race window
-- between two near-simultaneous submissions for the same track)
CREATE UNIQUE INDEX idx_requests_no_duplicate_pending
    ON requests (spotify_track_uri)
    WHERE status = 'pending';

-- Supports the rate-limit lookups in §6.1 (count recent requests per device_token / client_ip)
CREATE INDEX idx_requests_device_token_requested_at ON requests (device_token, requested_at);
CREATE INDEX idx_requests_client_ip_requested_at ON requests (client_ip, requested_at);
