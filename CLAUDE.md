# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

All 8 phases of SPEC.md §11 are complete: scaffold, Spotify OAuth, admin auth/config, public request form, public menu, admin approval workflow, background worker, security hardening, and this final polish pass. The app is feature-complete per SPEC.md.

Repo is live at https://github.com/Pilly170/event-playlist (public). CI, secret scanning/push protection, and branch protection (PR + passing `lint-test-scan` check required on `main`, no direct pushes) are all set up — **work happens on feature branches with a PR into `main`, never a direct push.** Still open per SPEC.md: configure Hostinger's Compose-from-URL against this repo, and verify Hostinger's actual TLS-termination behavior (§9/§12.1 — the `Caddyfile`'s `SITE_ADDRESS` env var is the toggle once that's known).

**Phase 1 implementation notes:**
- `app/spotify/oauth.py` — builds the Spotify authorize URL and exchanges/refreshes tokens via `httpx2` (see below). Pure functions, no DB/session dependency.
- `app/spotify/token_store.py` — encrypted save/load of the single-row `spotify_auth` table (`app/migrations/0001_spotify_auth.sql`).
- `app/spotify/auth_manager.py` — `get_valid_access_token()` is the auto-refresh orchestrator: returns the cached access token if not within 60s of expiry, otherwise refreshes and persists before returning. This is the function any future Spotify-calling code should use to get a token, rather than reading `token_store` directly.
- `app/spotify/client.py` — thin authenticated API wrapper (`get_currently_playing()` so far); extend this file for further Spotify endpoints (§5) rather than calling `httpx2` directly from routers.
- `app/routers/admin_spotify.py` — `/admin/spotify/{connect,callback,status}`, now gated by `require_onboarded_admin` (see Phase 2 notes below) — the "deliberately unauthenticated for now" comment from Phase 1 no longer applies.
- `app/dependencies.py` — `get_db`/`get_http_client`/`get_cipher`, overridden in tests via `app.dependency_overrides`. `get_db` is deliberately an **async generator**: a sync one would resolve on a threadpool-worker thread while async routes run on the event-loop thread, and `sqlite3.Connection` objects can't cross threads — keep any dependency that hands out a live `sqlite3.Connection` async, and keep the routes that consume it `async def` too, so both stay on the same thread. **This applies to every future router touching `db`, not just Phase 1/2's.**
- Runtime HTTP client is **`httpx2`**, not `httpx` — the older package is deprecated by Starlette's own `TestClient` as of the versions this project pins (see `requirements.txt`); `httpx2`'s `AsyncClient`/`MockTransport` API is a compatible continuation, used for both production calls and test mocking.
- `app/migrations/` uses one file per phase/concept (`0001_spotify_auth.sql`, `0002_admin_users.sql`, `0003_config.sql`, `0004_audit_log.sql`), tracked in a `schema_migrations` table applied at startup by `app/db.py:run_migrations()` — not a single consolidated `0001_init.sql`, so later phases add their own numbered files rather than editing these.

**Phase 2 implementation notes:**
- **"Forced password change on first login" has no dedicated column in SPEC.md §4's `admin_users` schema** — it's implemented by treating `last_login_at IS NULL` as "this admin has never completed onboarding." `record_login()` (which sets `last_login_at`) is deliberately **not** called at the moment of credential verification for a first-time login — only after the password is actually changed (`app/routers/admin_auth.py:change_password_submit`). If it were called at login time instead, a user could dodge the forced change entirely by closing the tab before changing their password, since the very next request would find `last_login_at` already set. Don't "simplify" this by moving `record_login` earlier — it would silently break the forced-change guarantee.
- Two auth dependencies in `app/security/session.py`, not one: `require_admin` (session exists) gates `/admin/login`-adjacent routes (change-password), while `require_onboarded_admin` (session exists AND `last_login_at IS NOT NULL`) gates everything else (`/admin/config`, `/admin/spotify/*`). Using the stronger one everywhere would lock a first-time admin out of the very page that lets them change their password.
- Default admin user is seeded at startup (`app/services/admin_seed.py`) only if `admin_users` is empty, with a random one-time password. **The password itself is never logged** — it's written to `<data dir>/initial_admin_password.txt` with `0600` permissions, and only that path is logged. Logs are commonly shipped to longer-retention, less access-controlled storage than the app's own data volume, so a secret landing in a log line is a materially bigger exposure than a restricted file next to the SQLite DB. The file is deleted automatically once the forced password change actually completes (`clear_initial_admin_password_file`, called from `admin_auth.py:change_password_submit`) — not a fixed default credential either way, since this is a public repo.
- `app/dependencies.py:get_database_path` exists so route code that needs the raw configured path (not a live connection) — like the password-file cleanup above — stays overridable in tests the same way `get_db` is. Reaching for `settings.database_path` directly inside a router is what caused a real bug during development: a test's dependency override only pointed `get_db` at a tmp file, so an unrelated line that read `settings.database_path` directly kept resolving to the real project `./data/` directory. If a route needs the DB path itself, inject it via `get_database_path`, don't read `settings` inline.
- Session cookies use Starlette's `SessionMiddleware` (signed, not server-side-stored) — `same_site="strict"` is hardcoded per SPEC.md §8, but `https_only` is driven by the new `SECURE_COOKIES` env var (default `false`) for the same reason `Caddyfile`'s `SITE_ADDRESS` is configurable: a `Secure` cookie is silently dropped by browsers over plain HTTP, so this can't default to `true` until Hostinger's TLS behavior (§12.1) is confirmed.
- **Explicitly deferred to Phase 7** ("security hardening pass" in SPEC.md §11, which literally says "everything in §8 not already covered incidentally"): login lockout after repeated failures, CSRF tokens on admin forms. Login failures are already audit-logged (`login.failure`), just not yet rate-limited.
- Config CRUD templates are intentionally unstyled (`app/templates/admin/*.html`) — SPEC.md §11 Phase 2 explicitly calls for "a (temporary, unstyled) admin route."

**Phase 3 implementation notes:**
- **`requests.reference_code` has no column in SPEC.md §4's literal schema listing either** (same kind of gap as Phase 2's forced-password-change column) — §6.1/§6.2 explicitly require generating one at submission and using it, not name-matching, as the public status-lookup key, so it needs its own persisted `UNIQUE` column. Added in `app/migrations/0005_requests.sql`.
- **`config.require_admin_approval`'s "off" position (auto-add without a human) is not implemented.** Every submission lands as `pending` regardless of this config value — the playlist-insertion mechanism that toggle would need to trigger doesn't exist until Phase 5. Don't treat a request "skipping the queue" as a bug report against Phase 3; it's an intentionally unimplemented config value, flagged inline in `public_form.py:submit`.
- **Track data is always re-fetched from Spotify server-side** (`get_track()`) at both `/request/select` and `/request/submit`, never trusted from client-supplied hidden form fields — SPEC.md §8 requires that no client-supplied data reaches the Spotify API unvalidated, and this is also what stops a tampered hidden field from inserting a request for a track the search never actually returned.
- **Duplicate-check only covers `status = 'pending'` so far**, not "added and not yet played/removed" (`app/models/requests.py:has_pending_duplicate`) — the latter needs `playlist_state`, which doesn't exist until Phase 5. Nothing can reach `status = 'added'` yet, so this is currently complete; extend it once Phase 5 introduces that status.
- **Rate limiting is device-token-first, not IP-first** (`app/services/rate_limit.py`): 5 requests per device token per 30 min is checked before the 30-per-IP backstop, deliberately, since venue WiFi puts many genuine requestors behind one shared IP.
- **The device-token cookie gotcha that cost real debugging time:** setting a cookie via FastAPI's injected `response: Response` parameter does nothing if the route returns its own `Response` object (e.g. a `TemplateResponse`) instead of a plain value FastAPI builds a response from — that placeholder's headers are only merged in the latter case. `app/security/device_token.py:set_device_token_cookie` must be called directly on whatever `Response` object the route is actually about to return, not on an injected placeholder. See `public_form.py:submit`'s `respond()` closure for the pattern.
- Renamed `SESSION_COOKIE_SECURE` → `SECURE_COOKIES` (`app/config.py:secure_cookies`) since this Phase's device-token cookie shares the exact same TLS-dependent `Secure`-flag rationale as Phase 2's admin session cookie — one flag, not two redundant ones for the same underlying concept.
- `htmx.min.js` (2.0.10) is vendored into `app/static/js/`, not loaded from a CDN — keeps the container fully self-contained per SPEC.md §3's "single container" design and avoids a public-facing page depending on third-party JS delivery.
- Search-as-you-type debouncing lives entirely in the `hx-trigger="input changed delay:400ms, search"` attribute in `form.html` — no JS beyond vendored htmx itself.

**Phase 4 implementation notes:**
- **No background poller exists yet, and `/menu` doesn't need one.** SPEC.md §11's Phase 4 "Done when" talks about views reflecting live state "within one poll interval," but that phrasing describes the full system once Phase 6's worker exists. For now, `GET /menu` just calls Spotify's currently-playing endpoint live on every page load (`get_now_playing()`) — that's at least as fresh as a poll-cached value would be, and there's nothing to build against yet since Phase 6 hasn't landed. Don't add a poller here; it belongs in Phase 6.
- `app/spotify/client.py` gained `get_now_playing()` (parses `get_currently_playing()`'s raw dict into a `NowPlaying(track, is_playing)`) and `get_playlist_tracks()` (paginated, skips null `track` entries — a playlist can contain removed/regionally-unavailable tracks that come back as `{"track": null}`). Both reuse the existing `TrackResult` dataclass and the private `_track_json_to_result` parser rather than introducing new shapes.
- The reference-code status lookup (`GET /menu?code=`) upper-cases the input before querying — codes are generated uppercase-only (`app/services/reference_code.py`), but there's no reason to make a user typing one back in match case exactly.
- Playlist view falls back to "No playlist has been configured yet" when `config.default_playlist_id` is unset, rather than erroring — an admin hasn't necessarily set one by this point in the build (that happens via `/admin/config`, Phase 2).
- No htmx on this phase's pages, unlike Phase 3's search-as-you-type — a one-shot reference-code lookup and traditional prev/next playlist pagination don't need live partial swaps, so they're plain `GET` forms/links. htmx stays vendored and available for whichever later phase actually needs it again.

**Phase 5 implementation notes:**
- **The insertion-offset computation happens synchronously at approval time, not via a background poller** — `app/services/playlist_ops.py:approve_request` fetches the full current playlist (`get_playlist_track_uris`, walks every page) and the live currently-playing track on every single approval, then computes `insert_position = current_index + insert_tracks_ahead` from that fresh data. SPEC.md §5 explicitly requires this ("always re-fetch... never trust a position calculated even a few seconds earlier"), and Phase 6's worker doesn't exist yet to maintain a running position, so there's nothing to read from except a live fetch. Don't replace this with a cached/poller-derived index without re-reading §5's position-drift reasoning first.
- **The "already in the playlist" duplicate check is a live Spotify fetch, not a `playlist_state` query** — a track added to the playlist manually, outside the app, would have no `playlist_state` row at all, so only the live playlist listing (which `get_playlist_track_uris` also needs for the position math above) can catch it. One fetch serves both purposes.
- **`playlist_state.source = 'default'` rows are never written by this phase.** SPEC.md §4's schema supports tracking pre-existing "backbone" playlist tracks via that source value, but nothing in Phase 5 needs to know which tracks are backbone vs. app-inserted — that distinction only matters for Phase 6's cleanup worker (which must never touch backbone tracks). Only `source = 'request'` rows exist until Phase 6 populates the rest.
- **`app/migrations/0006_playlist_state.sql` does not add a `REFERENCES requests(id)` foreign key**, even though `request_id` logically points there — matching how `requests.decided_by`'s link to `admin_users.username` is also just a comment-documented convention, not an enforced FK, elsewhere in this schema. An earlier draft of this migration added a real FK and the very first test run failed with `FOREIGN KEY constraint failed` (SQLite has `PRAGMA foreign_keys = ON` via `app/db.py:get_connection`) because test fixtures use arbitrary non-existent request IDs. Keep this schema convention (comment, not constraint) consistent if `playlist_state` gains more foreign-key-shaped columns later.
- Deny writes an audit log entry (`request.denied`) with the same actor/detail shape as approve's `request.approved`, even though the request row itself is deleted immediately after — the audit trail is the only surviving record of a denial, matching SPEC.md §6.3's "public status lookup just finds no matching reference code" behavior (already true since Phase 4, now for the right reason).
- Admin requests queue (`/admin/requests`) shows two sections — pending (with approve/deny buttons) and added (read-only) — but does **not** join against `playlist_state.played_at`/`removed_at` to show "has this track played yet." Phase 6 now populates those columns; adding that join to the admin UI is still outstanding, not blocked on anything.

**Phase 6 implementation notes:**
- **The poller is a single in-process `asyncio` task started/stopped in `app/main.py`'s lifespan** (`app/worker/poller.py:poll_forever`), not a separate process or container. This is a hard architectural constraint, not a convenience — SPEC.md §2/§6.4 require exactly one process running it (`uvicorn --workers 1`, one container replica), because multiple workers would each run their own poller, duplicating Spotify API calls and racing playlist edits against each other. Don't containerize or scale this out without first reading that constraint.
- **`run_poll_tick` is a pure, single-tick async function that returns the next `last_known_uri`**; `poll_forever` is a thin `while` loop around it holding the actual state between ticks. This split exists entirely for testability — every test in `test_poller.py` calls `run_poll_tick` directly with an explicit `last_known_uri` rather than needing to drive a real asyncio loop, except the two tests that specifically verify the loop's own stop/error-recovery behavior.
- **Repeat-mode enforcement is unconditional every tick** (`set_repeat_mode` called regardless of whether it changed), not edge-triggered on a detected config change. This is deliberate: it's what makes the playlist "run dry and loop back" fallback work at all — Spotify's own `repeat=context` handles the actual looping, so the worker doesn't need separate empty-playlist-detection/restart logic. It's also self-healing if the repeat state ever drifts for an unrelated reason (e.g. someone changes it from another device).
- **Track-finished detection compares `last_known_uri` (in-memory, reset on restart) against the current currently-playing URI, not a persisted "last polled" record.** Losing this state on a restart is an accepted, narrow edge case (one already-finished track could be missed for cleanup right at a restart boundary) rather than something this phase persists to the DB — SPEC.md doesn't call for restart-durability here, and adding it would be speculative scope beyond what's asked.
- **The "already in the playlist" pattern from Phase 5 repeats here for removal**: `_handle_track_finished` re-fetches the *live* playlist immediately before deleting, and removes by the freshly-found position (not the stale `inserted_position` from Phase 5, which has almost certainly drifted by the time a track finishes playing) — per SPEC.md §5's position-drift warning, now applied to removal as well as insertion.
- **Only tracks with an active (`removed_at IS NULL`) `source='request'` `playlist_state` entry are ever removed** (`get_active_entry_for_uri`). A finished backbone track with no matching entry is silently left alone — this is the enforcement point for "the default playlist is otherwise static," not a separate check elsewhere.
- **Partial-failure recovery is intentionally not built**: if `mark_played` succeeds but the subsequent Spotify removal call fails for some reason, that track won't automatically get retried on a later tick, since `last_known_uri` has already moved on to whatever's playing next. A reconciliation/retry sweep would need its own persisted queue of "played but not yet removed" entries — flagged here as a known gap rather than solved speculatively, since nothing in SPEC.md's Phase 6 description asks for it.

**Phase 7 implementation notes** (SPEC.md §8's checklist — see it for the full list; this covers what Phase 7 specifically added, not everything that was already incidentally done in earlier phases):
- **CSRF** (`app/security/csrf.py`): a per-session synchronizer token, stored in `request.session["csrf_token"]` and rendered as a hidden field in every state-changing *admin* form (change-password, config, logout, approve, deny). `verify_csrf_token` is a FastAPI dependency that declares `csrf_token: str = Form(...)` itself, rather than manually calling `request.form()` — this reuses FastAPI's own form-parsing/caching instead of risking a double-read of the request body. **Login is deliberately excluded** — the synchronizer-token pattern needs an existing session to bind the token to, which doesn't exist yet pre-authentication, and SPEC.md §8 scopes CSRF to "admin forms" specifically, not the login form itself.
- **Login lockout** (`app/services/login_lockout.py`): reuses the existing `audit_log` (already recorded every `login.failure`) via a new `count_recent_actions` query, rather than a new table or in-memory counter — the lockout is self-expiring for free once failures age out of the 15-minute window, no separate "locked until" timestamp needed. Checked *before* Argon2 password verification, so a locked-out attempt never even hashes the submitted password, and blocks **all** attempts once tripped (including a correct password), which is the entire point of a lockout. A distinct `login.blocked` audit action records the rejection separately from ordinary `login.failure` entries.
- **"Regenerate session ID on login"** (§8) doesn't map literally onto Starlette's `SessionMiddleware`, which has no server-side session ID at all — the whole session is a signed cookie value. `log_in()` (Phase 2) already calls `request.session.clear()` before setting the username, which is the closest available equivalent (any pre-login session data, CSRF token included, is discarded and regenerated fresh post-login) given this session backend. Don't read this as an unaddressed gap; it's the practical equivalent for this architecture, not a skipped requirement.
- **Content-Security-Policy required removing all inline `style="..."` attributes first** (`Caddyfile`'s `style-src 'self'` has no `'unsafe-inline'`) — three instances existed (two `style="display:inline"` on request-queue forms, one `style="color: darkred"` on the login error) and were moved to CSS classes (`.inline-form`, reusing the existing `.error`) specifically so the policy could stay strict rather than needing an escape hatch. `img-src` allows `https:` broadly (not just `'self'`) because album art URLs come directly from Spotify's own CDN.
- **Read-only root filesystem uncovered a real, pre-existing bug**, not something the read-only change itself caused: a fresh Docker named volume (`app_data:/data`) is created root-owned by the Docker daemon, and this container has never run as root — so it could never actually write to `/data` on a truly fresh volume, with or without `read_only: true`. Fixed in the `Dockerfile` by pre-creating `/data` with `appuser` ownership *before* `USER appuser` — Docker copies a mount point's existing content (ownership included) from the image into a volume the first time that volume is empty, which is what makes this work. Verified by tearing down and recreating the volume from scratch and confirming `ls -la /data` inside the container shows `appuser:appuser`, not `root:root`. `PYTHONDONTWRITEBYTECODE=1` was added alongside this so the app never attempts a `.pyc` write under the now-read-only root (harmless either way, but avoids the wasted attempt).
- **`.github/dependabot.yml`** covers `pip`, `docker`, and `github-actions` ecosystems, all on a weekly schedule — satisfies §8's "Pin dependency versions; Dependabot or equivalent for updates" now that versions have been pinned everywhere since Phase 0.
- Search query (`/request/search?q=`) and reference-code lookup (`/menu?code=`) both gained `Query(..., max_length=...)` bounds (100 and 20 chars respectively) — `requestor_name` already had one since Phase 3; this closes the same gap for the two remaining unbounded public query params.

**Phase 8 implementation notes** (styling pass, admin UX cleanup, README/runbook — SPEC.md §11):
- **Visual design system** (`app/static/css/style.css`): a "venue ticket/order-rail" concept — this app replaces a physical request slip and DJ-booth order rail, so the design leans into that directly (dashed-perforation ticket rows, stamp-style approve/deny buttons, a notched ticket-stub for the reference code) rather than a generic music-app look. Bold uppercase system-sans for headers/buttons, monospace for all *data* (track/artist, codes, timestamps, statuses), warm "aged ticket stock" palette in light mode with a dark "after-hours" variant via `prefers-color-scheme`. All colors/fonts are CSS custom properties on `:root` — change the palette by editing those, not by hunting through templates.
- **No inline `style="..."` attributes anywhere in `app/templates/`** — this is load-bearing, not a style preference: Phase 7's CSP sets `style-src 'self'` with no `unsafe-inline`, so a reintroduced inline style silently fails to apply in browsers that enforce CSP rather than erroring loudly. If you need one-off positioning, add a class to `style.css` instead (see `.ticket-meta`, `.inline-form` for examples of exactly this pattern).
- **`GET /` now redirects to `/request`** instead of rendering the old Phase-0 "scaffold deployed" placeholder page, which had become actively misleading once the app was feature-complete. `app/templates/placeholder.html` is gone; don't recreate it.
- **`/admin/spotify` (no trailing segment) is a new HTML page**, separate from the pre-existing `/admin/spotify/status` JSON endpoint (kept as-is, still covered by its original tests) — SPEC.md §6.3 calls for a connection-status-plus-reconnect-button page in the admin panel, and until Phase 8 there genuinely wasn't one; `/status` just returned raw JSON with no UI. Both routes read the same `load_tokens()` data; they're not in sync-drift risk because neither writes anything.
- **`app/templates/admin/_nav.html`** is a small partial (`{% include %}`'d into config/requests/spotify_status) providing consistent Requests/Config/Spotify/Log out navigation — it needs `csrf_token` in the including template's context (already true everywhere it's used, since those pages already needed one for their own forms). **Deliberately not included on `change_password.html`** — that page is a focused, single mandatory step; the nav's links are gated by `require_onboarded_admin` anyway and would just bounce back.
- **`SECURE_COOKIES` (added in Phase 7) was never actually wired into `docker-compose.yml`'s environment block** — a real gap found while writing the README's TLS section, not something Phase 7 flagged. Fixed by adding it there; without this fix, an operator following the SPEC.md §12.1 TLS-mode guidance to set `SECURE_COOKIES=true` once HTTPS was confirmed would have had no way to actually apply that setting in production.
- **README.md is now a full runbook**, not just a dev-setup doc — every command in it (seed-password retrieval, SQLite backup/restore via Python's `sqlite3` module, not the CLI, which isn't installed in `python:3.12-slim`) was run against a real `docker compose` stack while writing it, not just written from what seemed plausible. If you change the backup/restore procedure, re-verify it the same way rather than trusting the prose.

`SPEC.md` is the source of truth for this project. Read it in full before starting any phase of work — it contains locked-in decisions that should not be re-litigated without flagging the change explicitly. The summary below is a navigation aid, not a replacement for reading §1–§13 of `SPEC.md` directly.

## Commands

```bash
# Local dev (venv)
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
uvicorn app.main:app --reload

# Local dev (Docker Compose — docker-compose.override.yml builds from the local tree)
cp .env.example .env
docker compose up --build app     # add `caddy` too once TLS_MODE is decided

# Tests (pytest.ini sets pythonpath=. so both `pytest` and `python -m pytest` work)
pytest
pytest tests/unit/test_healthz.py::test_healthz_ok   # single test

# Lint / format / security (same checks CI runs, in .github/workflows/ci.yml)
ruff check .
black --check .            # `black .` to auto-fix
pip-audit -r requirements.txt
bandit -r app -ll
```

Note: `docker-compose.yml`'s `app.build.context` is a **git URL**, not `.` — this is required because Hostinger's Compose-from-URL deploy path fetches only that YAML file, never the surrounding repo (SPEC.md §9). `docker-compose.override.yml` is what makes local `docker compose up --build` use the working tree instead.

## Dependency malware scanning (`.github/workflows/guarddog.yml`)

A separate, **non-blocking** workflow runs DataDog's `guarddog` against every dependency (both `requirements.txt` and `requirements-dev.txt`, merged since `guarddog` doesn't follow the `-r requirements.txt` line) on every push/PR to `main`, uploading SARIF results to GitHub's Security > Code scanning tab. It is deliberately **not** part of the required `lint-test-scan` check and does **not** use `--exit-non-zero-on-finding`: that flag reacts to `guarddog`'s broad heuristic pattern matches (e.g. any `.read(`, `resolve(` call trips "capability" rules), which fire on essentially every real-world package — measured directly against this project's own dependencies, fastapi/jinja2/cryptography/uvicorn/httpx2 all triggered findings despite every one showing "0 risks detected." Treat this as a Security-tab triage feed, not a merge gate — don't "fix" the noise by adding `--exit-non-zero-on-finding` to CI without first tuning `--exclude-rules` extensively, or every PR will start failing on unrelated dependencies.

## What this app is

A self-contained web utility (FastAPI, single container) that lets venue attendees request songs via a public web form. Requests queue in an admin-only list; approving one inserts it into the venue's *live* Spotify playlist a configurable number of tracks ahead of whatever is currently playing. The app never touches playback hardware directly — it only calls the Spotify Web API, and the hardware is just another Spotify Connect device on the same account.

Key behavioral rule: only tracks the app itself inserted via an approved request are ever auto-removed (once played). The pre-existing "backbone" tracks of the default playlist are never touched by the cleanup worker — the default playlist is otherwise static and changes only via admin approval.

## Architecture (per SPEC.md §2)

```
Caddy (reverse proxy, auto-TLS) → FastAPI app → SQLite (volume-mounted)
                                        │
                                  Spotify Web API
```

Two containers in `docker-compose.yml`: `caddy` (reverse proxy/TLS) and `app` (FastAPI, serving public + admin + an in-process background poller). SQLite lives on a named Docker volume so it survives redeploys.

**Single-process constraint (critical):** the background poller (§6.4) is an in-process `asyncio` task, not a separate service. The app must run as exactly one process (`uvicorn --workers 1`, one `app` container replica). Running multiple workers/replicas duplicates Spotify API calls and races playlist edits from independent pollers. Do not scale this horizontally without first extracting the poller into its own single-instance worker container.

### Request lifecycle

1. Public form submission → row in `requests` with `status='pending'` (never touches the live playlist).
2. Admin approval → re-checks for duplicates against the *live* playlist (not just the DB, since a track could've been added manually outside the app), inserts at `current_index + insert_tracks_ahead`, writes a `playlist_state` row, marks the request `added`.
3. Admin denial → the `requests` row is deleted outright (not soft-deleted/status-flagged); a separate `audit_log` entry is the only remaining trace, for accountability without exposing a public "denied" state.
4. Background worker detects a tracked track has finished playing → removes it from the playlist, stamps `playlist_state.removed_at`.

Status is intentionally binary (`pending` | `added`) — there is no `approved` state distinct from `added` (approval and insertion happen atomically), and no `denied` state persists at all (§4 schema comment explains this).

### Spotify integration (§5)

Authorization Code Flow (not Client Credentials) — playlist mutation and playback-state reads need a user-scoped token. Admin authorizes once via the Admin panel; the app stores an encrypted refresh token and auto-refreshes.

No webhook exists for "track changed" — the app polls `GET /me/player/currently-playing` on `poll_interval_seconds` (default 15s) and reconciles state itself.

**Position drift is the main correctness hazard:** every playlist insert/remove returns a new `snapshot_id` and shifts positions. Always re-fetch/recompute current track order immediately before calculating an insertion position — never trust a position calculated even a few seconds earlier. When removing a played track, target by *position* (and the `snapshot_id` from insertion time), not URI alone, since the same track can legitimately appear twice.

Spotify enforces a rolling 30-second rate-limit window; back off on `429` using `Retry-After`, don't blind-retry.

### Rate limiting and anti-abuse (§6.1)

Primary rate-limit key is a per-browser device-token cookie (opaque, random, `httponly`, no PII) — **5 requests per device token per 30 minutes** — not client IP. This is deliberate: venue WiFi puts many genuine requestors behind one shared IP, so an IP-only limit would throttle the whole room. IP is kept only as a coarse backstop (~30/IP/30min) against a bot that clears cookies.

Duplicate-request prevention exists at two layers: a DB-level unique index (`idx_requests_no_duplicate_pending`, only one `pending` row per `spotify_track_uri`) plus an approval-time re-check against the live playlist state — closing the gap between "two simultaneous DB-level submissions" and "a track added manually outside the app."

## Planned repository structure (§13)

Routers stay thin; business logic (rate limiting, duplicate checks, playlist position math, crypto) lives in `app/services/`, unit-testable without spinning up FastAPI. Each phase in §11 maps to a specific slice of this tree — check §13 for the full layout before adding new modules, and follow its placement conventions (e.g. Spotify API calls go in `app/spotify/`, not inline in routers).

## Deployment model (§9) — affects how changes ship

- Hosting is Hostinger's Docker product via **Compose-from-URL**: Hostinger fetches only the `docker-compose.yml` from the repo, so `app`'s `build.context` must be a git URL (`https://github.com/<owner>/<repo>.git#main`), not a local path.
- Deploys are **manual**: merging to `main` does not auto-deploy. Someone must click "Update" in Hostinger's Docker Manager to rebuild/redeploy. CI (lint, tests, `pip-audit`/Snyk, `bandit`) gates the *merge* via branch protection, since it can't gate the deploy step itself.
- The repo must be **public** (Hostinger's build path can't authenticate against a private repo) — this is why GitHub secret scanning + push protection are a required Phase 0 setup step, not optional hardening.
- "Update" preserves the named SQLite volume; only application code changes on redeploy.

## Security constraints worth knowing before touching auth/data code (§8)

- Secrets (Spotify client ID/secret, token-encryption key, session-signing key) are injected as env vars through Hostinger's panel — never via a committed `.env`. `.env.example` documents shape only, for local dev.
- Spotify OAuth tokens are encrypted at rest (Fernet/AES-GCM), not stored in plaintext in SQLite.
- Admin auth: Argon2id password hashing, `httponly`/`secure`/`samesite=strict` session cookies, CSRF tokens on state-changing admin forms, lockout after 5 failed logins in 15 minutes.
- Track URIs reaching the Spotify API must always originate from the app's own search results, never raw client-supplied free text.
