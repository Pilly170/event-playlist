# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Phase 0 (scaffold) and Phase 1 (Spotify OAuth & token storage, SPEC.md §11) are complete. Phases 2–8 (data layer/config, public form, public menu, admin approval workflow, background worker, hardening, polish) are not yet implemented — `app/models/`, `app/services/` (beyond `crypto.py`), `app/security/`, `app/worker/` exist as empty package stubs per SPEC.md §13.

Repo is live at https://github.com/Pilly170/event-playlist (public). CI, secret scanning/push protection, and branch protection (PR + passing `lint-test-scan` check required on `main`, no direct pushes) are all set up — **work happens on feature branches with a PR into `main`, never a direct push.** Still open per SPEC.md: configure Hostinger's Compose-from-URL against this repo, and verify Hostinger's actual TLS-termination behavior (§9/§12.1 — the `Caddyfile`'s `SITE_ADDRESS` env var is the toggle once that's known).

**Phase 1 implementation notes:**
- `app/spotify/oauth.py` — builds the Spotify authorize URL and exchanges/refreshes tokens via `httpx2` (see below). Pure functions, no DB/session dependency.
- `app/spotify/token_store.py` — encrypted save/load of the single-row `spotify_auth` table (`app/migrations/0001_spotify_auth.sql`).
- `app/spotify/auth_manager.py` — `get_valid_access_token()` is the auto-refresh orchestrator: returns the cached access token if not within 60s of expiry, otherwise refreshes and persists before returning. This is the function any future Spotify-calling code should use to get a token, rather than reading `token_store` directly.
- `app/spotify/client.py` — thin authenticated API wrapper (`get_currently_playing()` so far); extend this file for further Spotify endpoints (§5) rather than calling `httpx2` directly from routers.
- `app/routers/admin_spotify.py` — `/admin/spotify/{connect,callback,status}`. **Deliberately unauthenticated for now** — Phase 2 must wrap `/admin/*` routes with session-auth middleware (§6.3); don't mistake the current lack of a login check for a finished feature.
- `app/dependencies.py` — `get_db`/`get_http_client`/`get_cipher`, overridden in tests via `app.dependency_overrides`. `get_db` is deliberately an **async generator**: a sync one would resolve on a threadpool-worker thread while async routes run on the event-loop thread, and `sqlite3.Connection` objects can't cross threads — keep any dependency that hands out a live `sqlite3.Connection` async, and keep the routes that consume it `async def` too, so both stay on the same thread.
- Runtime HTTP client is **`httpx2`**, not `httpx` — the older package is deprecated by Starlette's own `TestClient` as of the versions this project pins (see `requirements.txt`); `httpx2`'s `AsyncClient`/`MockTransport` API is a compatible continuation, used for both production calls and test mocking.
- `app/migrations/` uses one file per phase (e.g. `0001_spotify_auth.sql`), tracked in a `schema_migrations` table applied at startup by `app/db.py:run_migrations()` — not a single consolidated `0001_init.sql`, so later phases add their own numbered files rather than editing this one.

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
