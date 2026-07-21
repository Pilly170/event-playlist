# Spotify Playlist Request App — Spec-Driven Development Plan

## 0. Summary

A self-contained web utility that lets people request songs via a web form. Requests are queued for admin approval, then inserted into a live Spotify playlist a configurable number of tracks ahead of whatever is currently playing. The app only ever talks to the Spotify Web API — it never controls the playback hardware directly; that hardware is just another Spotify Connect device logged into the same account.

**The core idea of the approval step:** every suggestion lands first in an admin-only **request list** (all pending suggestions, visible in the admin section) — it does not touch the live playlist yet. Approving a request is what moves it out of that list and into the real, currently-playing default playlist, inserted the configured number of tracks ahead of whatever's playing. Denying it simply removes it from the request list — it never reaches the playlist at all. Once an approved track has actually played through, it's automatically removed from the playlist again (§6.4), so the playlist doesn't accumulate one-off requests over time; the pre-existing "backbone" tracks of the default playlist are never touched by that cleanup.

**Deployment target:** Hostinger's Docker hosting product — a `Dockerfile` + `docker-compose.yml` is the entire deployment artefact. No VPS provisioning, no Terraform, no separate cloud IaC layer.

**Decisions locked in from your answers:**
- Hosting: Hostinger Docker (Dockerfile + docker-compose.yml)
- Admin auth: local username/password
- Database: SQLite

---

## 1. Assumptions (flag if any are wrong)

1. The playback hardware runs the *default* Spotify playlist **in order, with shuffle off**. Without this, "insert X tracks ahead of current" has no stable meaning, since Spotify doesn't expose a shuffled queue's future order via the API.
2. The Spotify account behind the app is **Premium** — the `repeat` control and reliable playback-state endpoints require Premium.
3. One deployment = one venue = one Spotify account = one default playlist. Not multi-tenant.
4. "Removed once played/completed" means: once a track that the app is tracking has finished (currently-playing has moved past it), it's removed from the *default playlist*, not from the user's saved library or any other playlist.
5. The web form and menu are for anonymous members of the public (name is free text, not an authenticated identity) — only the Admin section needs a login.
6. SSL: Hostinger may terminate TLS itself at the platform level, or it may not — this needs verifying against your specific Hostinger plan before Phase 0 closes. The design accommodates either: Caddy is configured to attempt its own Let's Encrypt cert by default, but if Hostinger already terminates TLS in front of the container, Caddy (or the app) simply serves plain HTTP on the internal port and the automatic-HTTPS block in the Caddyfile is disabled via an env flag. See §9.
7. Spotify API access is via the **Authorization Code Flow** (not Client Credentials), because playlist modification and playback-state reads need a user-scoped token, not an app-only token. The admin authorises the app against the venue's Spotify account once, from within the Admin panel; the app stores and auto-refreshes the resulting refresh token.
8. No outbound webhook from Spotify exists for "track changed" — the app must **poll** `GET /me/player/currently-playing` on an interval (default suggested: every 10–15s) and reconcile state itself.

---

## 2. Architecture Overview

```
                        ┌────────────────────────────┐
                        │      Caddy (reverse proxy)  │
                        │   auto-TLS, ports 80/443    │
                        └──────────────┬──────────────┘
                                       │
                        ┌──────────────▼──────────────┐
                        │        FastAPI app           │
                        │  - Public web form/search     │
                        │  - Public "now playing" menu  │
                        │  - Admin panel (session auth)  │
                        │  - Spotify OAuth callback      │
                        │  - Background poller/worker    │
                        │    (asyncio task, in-process)  │
                        └──────────────┬──────────────┘
                                       │
                        ┌──────────────▼──────────────┐
                        │   SQLite (file, volume-mounted) │
                        │  requests | config | admin_users │
                        │  playlist_state | audit_log       │
                        └──────────────────────────────┘
                                       │
                                Spotify Web API
                     (search, playlists, player, currently-playing)
```

**Containers in `docker-compose.yml`:**
- `caddy` — reverse proxy + automatic HTTPS
- `app` — the FastAPI application (serves public + admin + runs the background poller in-process to start; split out to a separate worker container later if polling load ever justifies it)

SQLite lives on a named Docker volume, not inside the image, so it survives redeploys.

---

## 3. Tech Stack

| Concern | Choice | Why |
|---|---|---|
| Backend/API | Python 3.12, FastAPI | Matches your Python preference; async fits well with polling Spotify and serving requests concurrently |
| Templating/UI | Jinja2 + htmx + minimal CSS | Keeps it a single container, no separate frontend build pipeline, still gets live-feeling UI (queue/now-playing updates) without a JS framework |
| DB access | SQLModel or plain `sqlite3` + a thin repository layer | SQLite as decided; SQLModel gives you typed models without much ceremony |
| Background polling | `asyncio` task started on app startup | Simplest option for a single-instance deployment; avoids adding Redis/Celery for one periodic job |
| Reverse proxy/TLS | Caddy | Automatic Let's Encrypt with near-zero config, ideal for a small docker-compose deployment |
| Auth (admin) | Session cookie (signed, `httponly`, `secure`, `samesite=strict`) + Argon2 password hashing | Local username/password as decided |
| Secrets | Environment variables set via Hostinger's docker-compose editor (not a committed `.env`); `.env.example` documents required keys for local dev only | See §8 |
| Dependency scanning | Snyk (you already use this at work) or `pip-audit` in CI | Matches your existing tooling |

---

## 4. Data Model (SQLite)

```sql
-- Admin users
CREATE TABLE admin_users (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_login_at TEXT
);

-- Song requests
CREATE TABLE requests (
    id INTEGER PRIMARY KEY,
    spotify_track_uri TEXT NOT NULL,
    track_name TEXT NOT NULL,
    artist_name TEXT NOT NULL,
    is_explicit INTEGER NOT NULL DEFAULT 0,
    requestor_name TEXT NOT NULL,
    device_token TEXT NOT NULL,               -- opaque per-browser cookie value; primary rate-limit key (§6.1) — not an identity, no PII
    client_ip TEXT NOT NULL,                  -- coarse abuse backstop only (§6.1), not the primary rate-limit key
    status TEXT NOT NULL DEFAULT 'pending',   -- pending | added  (approval and insertion happen atomically, so there's no separate "approved" state; denied rows are deleted outright — see §6.3; post-insertion lifecycle, i.e. played/removed, lives in playlist_state, not here, to avoid tracking the same fact in two places)
    requested_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT,                          -- admin_users.username
    playlist_insert_position INTEGER          -- the playlist position it was inserted at, not a timestamp
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

-- Singleton config table (one row) — everything marked "configurable" in requirements
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

-- Encrypted Spotify OAuth tokens for the venue account
CREATE TABLE spotify_auth (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    access_token_enc BLOB NOT NULL,
    refresh_token_enc BLOB NOT NULL,
    expires_at TEXT NOT NULL,
    scope TEXT NOT NULL
);

-- Tracks the app knows it has inserted, to support safe cleanup
CREATE TABLE playlist_state (
    id INTEGER PRIMARY KEY,
    spotify_track_uri TEXT NOT NULL,
    source TEXT NOT NULL,          -- 'default' | 'request'
    request_id INTEGER,            -- FK -> requests.id, null if part of the pre-existing default set
    inserted_position INTEGER,     -- playlist position at time of insertion, for §5's position-drift handling
    snapshot_id_at_insert TEXT,    -- Spotify snapshot_id returned by the insert call
    added_at TEXT NOT NULL,
    played_at TEXT,
    removed_at TEXT
);

-- Audit trail — admin actions, config changes, auth events
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,           -- username or 'system'
    action TEXT NOT NULL,
    detail TEXT
);
```

Tokens are stored **encrypted at rest** (Fernet/AES-GCM using a key from the environment-variable secret, not the DB itself) — see §8.

---

## 5. Spotify Integration

**Auth:** Authorization Code Flow. Admin panel has a "Connect Spotify account" button → redirects to Spotify consent → callback stores encrypted refresh token. Required scopes:
- `playlist-read-private`, `playlist-modify-private`, `playlist-modify-public` (whichever matches the default playlist's visibility)
- `user-read-currently-playing`, `user-read-playback-state`
- `user-modify-playback-state` (needed for the repeat-mode control)

**Endpoints used:**
| Purpose | Endpoint |
|---|---|
| Search tracks (web form assist) | `GET /v1/search?type=track` |
| Get current playback | `GET /v1/me/player/currently-playing` |
| Get playlist items | `GET /v1/playlists/{playlist_id}/tracks` |
| Add track at position | `POST /v1/playlists/{playlist_id}/tracks` (with `position`) |
| Remove track | `DELETE /v1/playlists/{playlist_id}/tracks` |
| Set repeat mode | `PUT /v1/me/player/repeat` |
| Token refresh | `POST /accounts.spotify.com/api/token` |

**Rate limits:** Spotify enforces a rolling 30-second window rate limit per app; the poller must back off on `429` using the `Retry-After` header, not just retry blindly.

**Position drift and duplicate tracks:** every insert/remove call against a playlist returns a new `snapshot_id`, and playlist positions shift after every mutation. Two implications for the worker (§6.4):
- Always re-fetch (or recompute from the last known `snapshot_id`) the current track order immediately before calculating an insertion position — never trust a position calculated even a few seconds earlier.
- When removing a played track, target it by **position** (and ideally the `snapshot_id` from when it was added), not URI alone — the same track could legitimately appear twice (e.g. a request duplicates a backbone track), and removing "by URI" risks deleting the wrong occurrence.

---

## 6. Core Modules

### 6.1 Public web form
- Live search-as-you-type (htmx, debounced) against `/v1/search`, showing track, artist, album art, explicit flag.
- If `exclude_explicit` is enabled, explicit tracks are filtered out of search results entirely (not just blocked at submission — avoids a confusing "found it but can't request it" moment).
- Confirmation step shows the exact track before submission, plus requestor name field.
- On successful submission, a short, human-readable **reference code** (e.g. 5–6 characters) is generated and shown to the requestor — this is the recommended way to let them check status later, since matching purely on name is fragile if two people share a name or the same person requests twice. The code is stored alongside the request and is the lookup key for §6.2.
- **Rate limiting — per-browser token, not IP alone:** on first visit, the app sets an opaque, random, `httponly`, `secure` cookie (a device token, not an identity — no PII, long-lived, e.g. 1 year). This token, not the client IP, is the primary rate-limit key: **5 requests per device token per 30 minutes**. This matters because venue WiFi typically puts many genuine requestors behind one shared public IP — an IP-only limit would throttle the whole room collectively rather than each person. IP is kept only as a coarse abuse backstop (e.g. a much higher ceiling, like 30 requests per IP per 30 minutes, catching a bot that clears cookies to generate fresh tokens) rather than the primary control.
- A simple honeypot field is also kept as a second, independent layer against basic bots.
- **No duplicate active requests for the same track:** a request is rejected at submission if that track is already `pending`, or already `added` and not yet played/removed (i.e. currently sitting in the live playlist). The requestor sees a clear message ("Already queued — check the menu to see it") rather than a generic failure. Once a track has actually played and been removed (§6.4), it becomes requestable again — this isn't a permanent block, just a "don't stack the same song twice while it's still live" rule.

### 6.2 Public menu
- **Now playing** — polled state, shown read-only.
- **Is my request in the queue** — looked up by the reference code shown at submission (§6.1), not by name, to avoid clashes and ambiguity. Status shown as *pending* or *added* only — a denied request's row no longer exists (§6.3), so the lookup just shows "not found / not added" with no "denied" state ever surfaced publicly.
- **Full playlist view** — current default playlist contents, read-only, paginated.

### 6.3 Admin panel
- Login (local username/password, Argon2, session cookie, lockout after 5 failed attempts within 15 minutes).
- Request queue: approve/deny with one click. **Approve** re-checks for duplicates immediately before touching Spotify — the DB-level unique index (§4) already rules out two simultaneous *pending* duplicates, but it can't catch a track that was added to the playlist manually, outside the app entirely — if the track is already present anywhere in the current playlist (by URI, cross-checked against `playlist_state` rows with `removed_at IS NULL`), the approval is rejected with a clear "already in the playlist" message instead of silently creating a duplicate. If the check passes, it inserts into the playlist at `current_index + insert_tracks_ahead`, creates a `playlist_state` row, and marks the request `added`. **Deny** → the request row is deleted from the `requests` table/list entirely, matching the original requirement ("If denied, it is removed from the log/list"). A separate `audit_log` entry (who denied what, when) still gets written for security accountability — that's a distinct, admin-only forensic trail, not the visible request list, so it doesn't work against the "removed from the list" requirement.
- Denial is admin-only: since a denied request is deleted from `requests` outright, the public status lookup (§6.2) simply finds no matching reference code afterwards and shows "not added" — there's no lingering "denied" state to accidentally expose.
- Config screen: every item in §7 below, editable, validated, audit-logged on change.
- Spotify connection status + reconnect button.
- History/log view of all *surviving* requests (`pending`, `added`, and — via a join against `playlist_state` — whether an added track has since played and been removed). Denied requests won't appear here since their row is deleted (§6.3) — the only trace of a denial is the `audit_log` entry (who denied what, when), viewable separately as the accountability record rather than a working list.

### 6.4 Background worker (in-process asyncio task)
Runs every `poll_interval_seconds`:
1. Fetch currently playing track.
2. If it differs from last known: mark the previous track's `playlist_state` row `played_at`, and if it's eligible for cleanup (see below), remove it from the playlist via the API and stamp `removed_at`.
3. Recompute "current index" within the default playlist for the insertion-offset logic used by approvals.
4. If `playlist_repeat_enabled` changed, push the new repeat state to Spotify.
5. Handle empty-playlist edge case: if the playlist runs dry, this is exactly what `default_playlist_id` + repeat exist to prevent — worth a specific test case (§10).

**Cleanup rule (confirmed):** only tracks the app itself inserted via an approved request are ever removed automatically, once they've played. The default playlist is otherwise static — it's only ever changed by an admin approval — so pre-existing default-playlist tracks are never touched by the worker.

**Single-process constraint:** the poller is an in-process asyncio task, not a separate service. The app **must** run as a single process (`uvicorn` with `--workers 1`, and only one `app` container replica). Running multiple workers/replicas would start the poller multiple times, causing duplicate Spotify API calls and racing playlist edits from independent pollers. If load ever justifies horizontal scaling, the poller needs to move out into its own single-instance worker container first — flagged here so it isn't accidentally scaled up later without addressing this.

---

## 7. Admin-Configurable Items

Everything explicitly marked configurable in the original requirements, plus one added operational setting (`poll_interval_seconds`) that wasn't in the original list but is needed to run the background worker:

| Setting | Field | Default |
|---|---|---|
| Require admin approval before adding | `require_admin_approval` | on |
| Exclude explicit tracks | `exclude_explicit` | on |
| Default playlist | `default_playlist_id` | — set on first run |
| Insert requests X tracks ahead of current | `insert_tracks_ahead` | 3 |
| Playlist repeat | `playlist_repeat_enabled` | on |
| Poll interval *(not in original requirements — added for the worker)* | `poll_interval_seconds` | 15 |

---

## 8. Security Requirements

This is the section flagged explicitly in your requirements, so treating it as first-class rather than an afterthought:

**Secrets management**
- Since Hostinger's Docker product lets you edit `docker-compose.yml` and set environment variables directly through its own control panel, secrets (Spotify client ID/secret, the token-encryption key, the admin session-signing key) are injected that way rather than via a `.env` file baked into the image or committed to the repo. The compose file just references `${SPOTIFY_CLIENT_ID}` etc.; the actual values live only in Hostinger's panel. This is simpler than managing a `.env` file on the host and avoids a secrets file sitting on disk at all.
- **The repo is public** (§9, required by Hostinger's git-context build path), which raises the bar here: enable **GitHub's secret scanning and push protection** (free on public repos) so an accidental commit of a real token or key is blocked before it ever lands, not caught after the fact. Add this as an explicit Phase 0 setup step, not an afterthought.
- Locally (dev machine), a `.env.example` with placeholder keys is still committed so the shape of required config is documented, but the real `.env` stays gitignored for local runs only.
- OAuth tokens are still encrypted at rest in SQLite (Fernet/AES-GCM, keyed from the env-var secret above), not stored in plaintext even though the DB file itself should also be access-restricted.
- No secrets in logs, ever — explicit log-scrubbing check in code review.

**AuthN/AuthZ**
- Argon2id for admin password hashing.
- Session cookies: `httponly`, `secure`, `samesite=strict`, short-ish expiry, regenerate session ID on login.
- CSRF tokens on all state-changing admin forms.
- Login rate-limiting / lockout after repeated failures.
- Admin routes never reachable without a valid session — enforced centrally (middleware/dependency), not per-route.

**Public-facing surface**
- Input validation and length limits on requestor name and search query (prevent injection/XSS — Jinja2 autoescaping stays on).
- The rate-limiting device-token cookie (§6.1) is set for anti-abuse purposes only, contains no PII, and isn't used for tracking or analytics — this keeps it within the "strictly necessary" exemption under UK PECR, so it shouldn't need a cookie-consent banner. Worth a second opinion if this ever gets scrutinised properly, but it's the standard basis other sites rely on for similar security/fraud-prevention cookies.
- No user-supplied data ever reaches the Spotify API un-validated (track URIs are only ever ones the app's own search returned, not free text from the client).

**Transport/network**
- HTTPS only, HTTP requests redirected — handled by Caddy.
- Security headers: `Strict-Transport-Security`, `X-Content-Type-Options`, `X-Frame-Options`/`frame-ancestors`, a reasonable `Content-Security-Policy`.

**Container hardening**
- App container runs as a non-root user.
- Base image pinned and minimal (`python:3.12-slim`), rebuilt regularly.
- Read-only root filesystem where feasible, with an explicit writable volume for the SQLite file.
- No unnecessary ports exposed beyond what Caddy needs.

**Dependency/code security**
- Snyk (or `pip-audit`) run in CI on every change, given this matches your existing DevSecOps tooling.
- Pin dependency versions; Dependabot or equivalent for updates.
- A basic SAST pass (e.g. `bandit`) before the security review milestone.

**Audit & accountability**
- Every admin approve/deny/config-change/login event written to `audit_log`.

---

## 9. Deployment (Hostinger Docker)

**Source control & deployment:** source lives in a **public** GitHub repository — required, since Hostinger's git-context build path has no way to authenticate against a private one (see below). Deployment uses Hostinger's built-in **Compose from URL** feature — pasting the link to the repo's `docker-compose.yml` into hPanel's Docker Manager. Confirmed: Hostinger only ever fetches that YAML file itself, not the surrounding repo — so the `app` service's `build:` context can't be a local path like `.`; it must point at the git repository directly, using Compose/BuildKit's git-context syntax:

```yaml
services:
  app:
    build:
      context: https://github.com/<owner>/<repo>.git#main
      dockerfile: Dockerfile
```

This is confirmed as a **one-time build on initial deploy** — it does not redeploy automatically on new merges to `main`. Hostinger's Docker Manager has an "Update" action that re-pulls the compose file and rebuilds (picking up whatever the referenced git ref currently points at), but triggering it is a **manual step**, not something that fires on push.

Because of that, the security-scan gate (Snyk/pip-audit/bandit, per §8) sits **before merge**: a GitHub Actions workflow runs those checks on every push/PR, and a branch-protection rule on `main` requires them to pass before merging — so nothing unscanned reaches the branch the compose file's git-context points at. The manual "Update" click in hPanel is then the actual go-live moment, and needs documenting clearly in Phase 8's runbook as an explicit release step: *merge → CI green → someone clicks Update in Hostinger's Docker Manager*. Worth deciding whether that's always you personally for now, or something you want scripted later (if hPanel exposes an API for it).

**Confirmed:** "Update" preserves the named SQLite volume and rebuilds/replaces the running image — data (requests, config, stored Spotify tokens) survives a redeploy; only the application code changes.

**Confirmed:** the repo is **public**. Hostinger's Compose-from-URL build path has no mechanism for supplying a PAT or deploy key to authenticate the git-context build, so a private repo isn't workable here — the code itself has to be public for this deploy path to build at all. This has no bearing on runtime secrets (Spotify client ID/secret, encryption keys) — those never live in the repo regardless, only as env vars in Hostinger's panel (§8) — but it does raise the bar on making sure nothing sensitive ever accidentally lands in a commit, since there's no "just don't share the repo" fallback if something slips through. See §8 for the added safeguard this brings in.

`docker-compose.yml` (two services): `caddy` and `app`, sharing a network; `app` mounts a named volume for the SQLite file; `caddy` mounts a volume for its certificate store and reads a `Caddyfile` pointing your domain at `app:8000`. Secrets referenced as `${VAR}` in the compose file, values set through Hostinger's own compose/env editor rather than a file on disk (§8).

**TLS mode — needs a Phase 0 verification step:** confirm whether Hostinger's Docker product terminates TLS in front of your container.
- If it doesn't: Caddy runs its normal automatic-HTTPS mode (Let's Encrypt HTTP-01), needs 80/443 reachable.
- If it does: Caddy (or even just the app's own web server) serves plain HTTP internally on whatever port Hostinger proxies to, and the Let's Encrypt block is skipped entirely — set via a `TLS_MODE=external|caddy` env var read by the Caddyfile/entrypoint so switching later is a one-line config change rather than a rebuild.

`Dockerfile` for `app`: multi-stage build — a builder stage `COPY`s `requirements.txt` and source, installs dependencies, then only the installed app is `COPY`'d into a slim final stage (not `ADD`, which is reserved for remote URLs/archive extraction and isn't needed here); non-root `USER`, `HEALTHCHECK` hitting a `/healthz` route.

**SQLite backup:** the database is a single file on a named volume with no built-in redundancy. Add a simple periodic backup (e.g. a cron-triggered `sqlite3 .backup` copy to a separate location, or Hostinger's own volume snapshot if it offers one) — this is small enough in scope to fold into Phase 8's runbook, but worth deciding on *before* go-live rather than after a first data loss.

No provisioning scripts, no Terraform — Hostinger's Docker product consumes the Dockerfile/compose file directly, per your correction.

---

## 10. Testing & Security Review Plan

- Unit tests: config validation, insertion-offset arithmetic, explicit-filter logic, playlist cleanup eligibility rules, duplicate-track rejection at both submission and approval time (including the race-condition case of two near-simultaneous submissions for the same track), rate-limit enforcement by device token independent of shared IP (simulate many device tokens behind one IP staying under the per-token limit but exceeding the IP backstop).
- Integration tests against a mocked Spotify API (record/replay fixtures) — covering token refresh, rate-limit backoff, and the "playlist runs dry" edge case.
- Manual security review pass mapped against OWASP ASVS or Top 10 before go-live, specifically covering the items in §8.
- Load/soak test of the polling loop over a multi-hour period to catch token-refresh or memory issues that only show up over time.

---

## 11. Phased Build Plan

Each phase is scoped to be handed to a developer (or an AI coding agent) as a self-contained unit with clear acceptance criteria.

**Phase 0 — Scaffold**
Public GitHub repo (required by Hostinger's git-context build path, §9) with **secret scanning and push protection enabled**; repo structure per §13; `Dockerfile`; `docker-compose.yml` (with the `app` service's `build.context` pointing at the git repo, per §9) with `caddy` + `app` stub; `.env.example`; GitHub Actions CI (lint, test, `pip-audit`/Snyk, `bandit`) as a required branch-protection check on `main`; Hostinger Docker Manager configured with Compose-from-URL pointing at the repo's `docker-compose.yml`.
*Done when:* `main` is protected by a passing CI gate, secret scanning is confirmed active, and a manual "Update" in Hostinger's Docker Manager successfully builds and serves a placeholder page over HTTPS from the deployed compose stack.

**Phase 1 — Spotify OAuth & token storage**
Admin "connect Spotify" flow, encrypted token storage, auto-refresh.
*Done when:* admin can authorise once and the app can make an authenticated API call after a token expiry/refresh cycle.

**Phase 2 — Data layer & config**
SQLite schema + migrations, config CRUD, seed default admin user (forced password change on first login).
*Done when:* config values persist and are editable via a (temporary, unstyled) admin route.

**Phase 3 — Public search & request form**
Search-as-you-type, explicit filtering, confirmation step, submission with rate limiting.
*Done when:* a request lands in `requests` with status `pending`.

**Phase 4 — Public menu**
Now playing, "is mine queued", playlist view.
*Done when:* all three read-only views reflect live Spotify state within one poll interval.

**Phase 5 — Admin approval workflow**
Approve/deny actions wired to real playlist insertion at the configured offset; audit logging.
*Done when:* approving a request visibly inserts it into the live playlist at the right position.

**Phase 6 — Background maintenance worker**
Poller: track-change detection, cleanup of played requested tracks, repeat-mode enforcement, empty-playlist fallback.
*Done when:* letting the playlist run down to the last track and beyond correctly falls back to the default set with repeat, and played requested tracks are removed.

**Phase 7 — Security hardening pass**
Everything in §8 not already covered incidentally: headers, CSRF, lockouts, container non-root, dependency scan clean.
*Done when:* a manual OWASP-style review finds no unresolved high/critical items.

**Phase 8 — Polish & handover**
Styling pass, admin UX cleanup, README/runbook (how to rotate the Spotify client secret, how to restore from an SQLite backup, how to change the default playlist, and the manual release process: merge → confirm CI is green → click "Update" in Hostinger's Docker Manager).
*Done when:* someone unfamiliar with the code can deploy from scratch, and release a new change, using only the README.

---

## 12. Decisions Confirmed

1. **TLS:** Hostinger's Docker product may or may not terminate TLS itself — build to support both modes (§9), verify the actual answer as a Phase 0 task before relying on either.
2. **Cleanup scope:** only app-inserted (approved-request) tracks are auto-removed once played; the default playlist is otherwise static and only changes via admin approval.
3. **Queue status lookup:** reference code issued at submission, not name-matching (§6.1, §6.2).
4. **Rate limiting:** 5 requests per requestor (by IP) per 30 minutes.
5. **Denial:** the request row is deleted from the log/list entirely (per the original requirement), leaving only a separate admin-only audit trail entry — not a visible "denied" status anywhere the public can see (§6.3).
6. **Deployment method:** Hostinger's built-in Compose-from-URL, which only fetches the compose file itself — so `build.context` must reference the git repo directly, and rebuilds are a **manual "Update" click** in hPanel, not automatic on merge (§9). Security scanning gates merges to `main` via branch protection instead of gating the deploy step itself. **Confirmed:** "Update" preserves the named SQLite volume and just rebuilds/replaces the image — data survives redeploys. **Confirmed:** the repo must be **public**, since Hostinger's build path has no way to authenticate a git-context build against a private repo — mitigated by enabling GitHub's secret scanning/push protection (§8) as a Phase 0 setup step.
7. **Rate limiting:** keyed by a per-browser device-token cookie as the primary limit (5 requests/30 min), not IP — since venue WiFi typically puts many genuine requestors behind one shared IP. IP is kept only as a coarse abuse backstop (§6.1, §4).
8. **Duplicate concurrent requests:** confirmed — blocked. If a track is already `pending` or `added`, a second person's request for the same track is rejected; first request wins (§6.1).

---

## 13. Repository Structure

Laid out so each phase in §11 maps to a specific set of files, and routers stay thin — the actual logic (rate limiting, duplicate checks, playlist insertion/position math, crypto) lives in `services/`, testable without spinning up FastAPI at all.

```
spotify-request-app/
├── .github/
│   └── workflows/
│       └── ci.yml                  # lint, tests, pip-audit/Snyk, bandit — required branch-protection check (§9)
├── app/
│   ├── main.py                     # FastAPI app factory; starts the poller task on startup (§6.4)
│   ├── config.py                   # pydantic settings, reads env vars (§8) — never reads a committed .env
│   ├── db.py                       # SQLite connection/session setup
│   ├── migrations/
│   │   └── 0001_init.sql           # the schema in §4, applied at startup
│   ├── models/                     # one file per table in §4
│   │   ├── requests.py
│   │   ├── config.py
│   │   ├── admin_users.py
│   │   ├── playlist_state.py
│   │   ├── audit_log.py
│   │   └── spotify_auth.py
│   ├── spotify/
│   │   ├── client.py               # thin wrapper around the endpoints in §5
│   │   ├── oauth.py                # Authorization Code flow, refresh (Phase 1)
│   │   └── exceptions.py           # rate-limit/429 handling (§5)
│   ├── services/                   # the actual business logic, unit-testable in isolation
│   │   ├── rate_limit.py           # device-token + IP backstop (§6.1, decision 7)
│   │   ├── duplicates.py           # submission- and approval-time checks (§6.1, §6.3)
│   │   ├── playlist_ops.py         # insertion offset, position drift, snapshot handling (§5, §6.4)
│   │   ├── crypto.py                # Fernet/AES-GCM token encryption (§8)
│   │   └── audit.py                 # audit_log writer helper
│   ├── security/
│   │   ├── auth.py                 # session cookie, Argon2, lockout (§6.3, §8)
│   │   └── csrf.py
│   ├── routers/                    # thin — parse request, call a service, render/return
│   │   ├── public_form.py          # Phase 3
│   │   ├── public_menu.py          # Phase 4
│   │   ├── admin_auth.py           # Phase 2
│   │   ├── admin_requests.py       # Phase 5
│   │   ├── admin_config.py         # Phase 2
│   │   └── healthz.py              # Phase 0
│   ├── worker/
│   │   └── poller.py               # Phase 6 — the single-process asyncio task
│   ├── templates/                  # Jinja2 + htmx
│   │   ├── base.html
│   │   ├── public/
│   │   │   ├── form.html
│   │   │   └── menu.html
│   │   └── admin/
│   │       ├── login.html
│   │       ├── requests.html
│   │       └── config.html
│   └── static/
│       └── css/
├── tests/
│   ├── unit/                       # mirrors services/ — §10's unit test list
│   ├── integration/
│   │   └── fixtures/               # recorded Spotify API responses, per §10
│   └── conftest.py
├── Dockerfile                      # §9
├── docker-compose.yml              # §9 — build.context points at this repo's git URL
├── Caddyfile
├── requirements.txt
├── requirements-dev.txt            # pytest, bandit, ruff/black, pip-audit
├── .env.example                    # documents required keys, no real values (§8)
├── .gitignore                      # excludes local .env
└── README.md                       # Phase 8 runbook
```
