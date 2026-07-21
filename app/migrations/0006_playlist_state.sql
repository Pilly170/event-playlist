-- Tracks the app knows it has inserted (or, later, that pre-exist in the default
-- playlist), to support safe cleanup once Phase 6's worker exists (SPEC.md §4).
-- Phase 5 only ever writes source='request' rows here — it does not attempt to
-- snapshot the pre-existing 'default' backbone tracks; that's Phase 6's concern.
CREATE TABLE playlist_state (
    id INTEGER PRIMARY KEY,
    spotify_track_uri TEXT NOT NULL,
    source TEXT NOT NULL,          -- 'default' | 'request'
    request_id INTEGER,             -- FK -> requests.id, null if part of the pre-existing default set
    inserted_position INTEGER,     -- playlist position at time of insertion, for §5's position-drift handling
    snapshot_id_at_insert TEXT,    -- Spotify snapshot_id returned by the insert call
    added_at TEXT NOT NULL,
    played_at TEXT,
    removed_at TEXT
);
