-- Singleton config table (one row) — everything marked configurable in SPEC.md §7
CREATE TABLE config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    require_admin_approval INTEGER NOT NULL DEFAULT 1,
    exclude_explicit INTEGER NOT NULL DEFAULT 1,
    default_playlist_id TEXT,
    insert_tracks_ahead INTEGER NOT NULL DEFAULT 3,
    playlist_repeat_enabled INTEGER NOT NULL DEFAULT 1,
    poll_interval_seconds INTEGER NOT NULL DEFAULT 15,
    updated_at TEXT NOT NULL
);

INSERT INTO config (id, updated_at) VALUES (1, strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'));
