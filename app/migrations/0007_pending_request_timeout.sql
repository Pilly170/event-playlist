-- Pending-request auto-approval + reminder email
-- (docs/superpowers/specs/2026-07-25-pending-request-auto-approval-design.md).
-- pending_request_timeout_minutes: admin-configurable, how long a request can sit
-- pending before the background timeout loop auto-approves it.
-- reminder_sent_at: tracks whether the one-time reminder email has already gone out
-- for a given request, so the timeout loop doesn't resend it every tick during the
-- warning window before auto-approval fires.
ALTER TABLE config ADD COLUMN pending_request_timeout_minutes INTEGER NOT NULL DEFAULT 15;
ALTER TABLE requests ADD COLUMN reminder_sent_at TEXT;
