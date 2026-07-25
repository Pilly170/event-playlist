# Pending-request auto-approval + reminder email

**Status:** approved by user, ready for implementation planning
**Date:** 2026-07-25

## Problem

Every submitted request sits in `pending` until an admin manually approves or denies it (`app/services/playlist_ops.py`). At a busy event this can build up into a large backlog if no admin is actively watching the queue, and there's currently no way for an admin to find out a queue exists other than opening `/admin/requests` themselves.

## Goals

- A request that's sat `pending` for too long gets auto-approved (added to the live playlist) rather than piling up forever.
- The admin gets a one-time reminder email 2 minutes before any individual request auto-approves, giving them a last chance to review/deny it first.
- The timeout duration is admin-configurable, matching every other tunable setting in this app.
- The notification recipient is visible on the Config page (so an admin can confirm where reminders go) but not editable there — it's a deployment-time setting, same as the SMTP credentials, not something that should be changeable by whoever's logged into the admin panel at a given moment.
- No new required infrastructure beyond a generic SMTP account, configured the same way Spotify's secrets already are (env vars via Hostinger's panel).

## Non-goals

- Not touching `config.require_admin_approval` (an existing, still-unimplemented flag for "auto-add with no human at all" — a different concept from this feature, which only auto-approves after a genuine timeout with no admin action).
- Not batching multiple stale requests into one digest email — confirmed with the user: one reminder per request, tied to that request's own timeout.
- Not adding "business hours" or any scheduling logic around the timeout — it's a flat wall-clock duration from `requested_at`.
- Not adding email as a new notification channel for anything else (denials, approvals, etc.) — scope is strictly the pending-request reminder.

## Architecture

### A second, independent background loop

The existing Spotify poller (`app/worker/poller.py`) already establishes the pattern this app uses for in-process background work: a pure `run_poll_tick()` function (easy to unit-test directly) wrapped by a thin `poll_forever()` loop, started as an `asyncio.create_task` in `app/main.py`'s lifespan and stopped via an `asyncio.Event`.

This feature reuses that exact pattern as a **second, independent task** — `app/worker/request_timeout.py` with `run_timeout_tick()` / `timeout_loop_forever()` — rather than folding this logic into the existing poller tick. The two loops have nothing to do with each other: the Spotify poller's job is reconciling live playback state, and this one only ever reads/writes the `requests` table and (on timeout) calls the same `approve_request()` the manual admin-approval button already uses. Bundling them would blur two unrelated responsibilities into one function for no benefit — SPEC.md's single-process constraint is about not running multiple *processes* polling Spotify concurrently; a second `asyncio` task inside the same process doesn't touch that constraint at all, same as this app already runs the web server and the Spotify poller concurrently today.

Both tasks are started/stopped the same way in `app/main.py`'s lifespan, each with its own `stop_event`.

**Tick interval:** reuses `config.poll_interval_seconds` as its wait interval too — no need for a second interval setting; a 15-second default cadence is more than frequent enough to catch a 2-minute reminder window or a multi-minute timeout accurately.

### Data model changes

**`config` table** (new migration, extending the existing single-row settings table exactly like every other admin-tunable value already there):
- `pending_request_timeout_minutes INTEGER NOT NULL DEFAULT 15`

Admin-editable on the existing `/admin/config` page, added to `app/models/config.py`'s `_EDITABLE_FIELDS`/`_SELECT_CONFIG`/`AppConfig`, following the exact pattern `insert_tracks_ahead` and `poll_interval_seconds` already use.

**`requests` table** (same migration): `reminder_sent_at TEXT` (nullable timestamp). Tracks whether the one-time reminder has already gone out for this request, so the tick doesn't re-send it every 15 seconds during the 2-minute warning window. Set once, never cleared.

**Notification recipient is not a DB field.** It's `NOTIFICATION_EMAIL`, a new setting in `app/config.py` alongside the SMTP settings below — deployment-time only, set via Hostinger's env panel like every other secret/deployment setting in this app. The `/admin/config` page renders it as a `disabled` input pre-filled with the current value (or a placeholder like "not configured" if blank) purely for visibility — a `disabled` input is never included in form submission, so there's no server-side risk of it being editable via a crafted request either, not just a UI-level restriction.

### Tick logic (`run_timeout_tick`)

Each tick, using `datetime.now(timezone.utc)`:

1. **Reminders**: if `timeout_minutes > 2`, find `pending` requests where `reminder_sent_at IS NULL` and `requested_at <= now - (timeout_minutes - 2) minutes`, then send the reminder email for each (only if `settings.notification_email` is set) and set `reminder_sent_at = now`. If `timeout_minutes <= 2` there's no meaningful warning window to give, so the reminder step is skipped entirely for that request rather than firing immediately or backwards — auto-approval (step 2) still applies on schedule regardless. A send failure is logged and does **not** block later steps — same "isolate failures, don't let one thing wedge the rest of the tick" principle the poller already applies to `set_repeat_mode`.
2. **Auto-approvals**: find `pending` requests where `requested_at <= now - timeout_minutes minutes`. For each, call the existing `approve_request(..., admin_username="system")` — identical code path (live re-fetch, position math, `playlist_state` row, `requests.status` update) to a manual admin approval, just with `"system"` recorded as `decided_by`/audit actor, matching the existing convention for the poller's own automated playlist mutations (`playlist.track_removed`). If `approve_request` raises (e.g. no default playlist configured, track already in the playlist), log and move on to the next request rather than crashing the tick — a request that can't be auto-approved right now will simply be retried next tick until it either succeeds or an admin manually intervenes.

Both steps query fresh from the DB every tick (no in-memory state carried between ticks), so a restart loses nothing — same acceptable tradeoff already documented for the Spotify poller's `last_known_uri`.

### Email delivery

New module `app/services/notifications.py`, using Python's stdlib `smtplib` + `email.message.EmailMessage` — no new dependency. New settings (`app/config.py`, alongside the existing Spotify/session secrets): `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_ADDRESS`, and `NOTIFICATION_EMAIL` (the recipient — see the data model section above for why this is a setting rather than a DB field). All optional/blank by default; if `SMTP_HOST` or `NOTIFICATION_EMAIL` isn't set, sending is skipped with a logged warning rather than raising (keeps local dev and any deploy that hasn't configured email yet from breaking).

Reminder email content: track name/artist, requestor name, reference code, and a plain link to `/admin/requests` so the admin can act immediately. Sent as plain text — no HTML templating needed for a single-purpose internal notification.

## Error handling

- SMTP send failures: logged, tick continues (see above).
- `approve_request` failures during auto-approval: logged, tick continues, retried next tick.
- Missing `NOTIFICATION_EMAIL` or SMTP config entirely: reminder step is a no-op; auto-approval still proceeds on schedule regardless (the timeout is the actual backlog-prevention mechanism; the email is just a courtesy heads-up).

## Testing

Following this codebase's existing test conventions (`tests/unit/test_poller.py` as the direct template):
- `run_timeout_tick` unit tests with a fake/mocked SMTP send and an in-memory DB, covering: reminder fires once and only once per request, auto-approval fires and reuses `approve_request`, a failed auto-approval doesn't block other requests in the same tick, no `NOTIFICATION_EMAIL` configured skips reminders without affecting auto-approval.
- `app/services/notifications.py` unit tests with a mocked `smtplib.SMTP` (or a fake transport if stdlib allows it cleanly) verifying message content and that a missing `SMTP_HOST`/`NOTIFICATION_EMAIL` no-ops rather than raising.
- `app/models/config.py` additions covered by extending the existing config model tests (just `pending_request_timeout_minutes` now).
- Admin config form: extend existing router tests to cover the new timeout field round-tripping through `/admin/config`, and confirm the notification-email field renders `disabled` and is a no-op if included in a submitted form body regardless.

## Deployment

New required manual step, same shape as the existing Spotify secrets: set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_ADDRESS`, `NOTIFICATION_EMAIL` in Hostinger's environment panel and add them to `docker-compose.yml`'s `app.environment` block (and `.env.example` for local dev documentation) — no other infrastructure changes; this rides on the same registry-image + `pull_policy: always` deploy pipeline already fixed earlier.
