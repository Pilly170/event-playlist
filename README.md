# Event Playlist

A self-contained web app that lets venue attendees request songs, queues them for admin approval, and inserts approved tracks into a live Spotify playlist a configurable number of tracks ahead of whatever's currently playing. A background worker removes requested tracks once they've played and keeps the playlist looping via Spotify's own repeat mode, so it never runs dry.

See [`SPEC.md`](./SPEC.md) for the full spec-driven build plan and [`CLAUDE.md`](./CLAUDE.md) for an architecture summary aimed at whoever (human or AI) picks up development next.

This repository has completed all 8 phases of the build plan (SPEC.md §11): scaffold, Spotify OAuth, admin auth/config, the public request form and menu, the admin approval workflow, the background worker, a security hardening pass, and this polish pass.

## What you need before you start

- A Spotify account with **Premium** (required for playback-state and repeat-mode control) that's logged into the venue's playback hardware as a Spotify Connect device
- A [Spotify Developer](https://developer.spotify.com/dashboard) account, to register an app and get a Client ID/Secret
- A Hostinger account with the Docker hosting product (SPEC.md §9 — Compose-from-URL); this deployment relies on a separate Traefik project already running on the VPS for real domain routing/TLS — see [step 4](#4-set-up-hostinger)
- A **public** GitHub repository containing this code (required — see [Deployment model](#deployment-model) below for why)

## First deploy, from scratch

### 1. Register a Spotify app

Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard), create an app, and note its **Client ID** and **Client Secret**. Add a **Redirect URI** matching exactly:

```
https://<your-domain-or-vps-hostname>/admin/spotify/callback
```

(use `http://localhost:8000/admin/spotify/callback` for local development only — see [step 4](#4-set-up-hostinger) for how production TLS/routing actually works on this deployment).

### 2. Get this code onto a public GitHub repo

Hostinger's Compose-from-URL deploy path fetches only `docker-compose.yml` directly from GitHub — it never clones the surrounding repo, so the repo has to be public for the fetch to work at all (see [Deployment model](#deployment-model)). Push this repo there if you haven't already.

`docker-compose.yml` references pre-built images (`ghcr.io/<owner>/event-playlist-app:latest`, `event-playlist-caddy:latest`) rather than building from source — `.github/workflows/publish-images.yml` builds and pushes those on every merge to `main`. If you fork this repo, update the `image:` lines to your own GHCR namespace, and make sure the packages are set to **public** visibility once the workflow has pushed them at least once (Hostinger's pull is unauthenticated, same as the repo fetch above).

Enable **GitHub secret scanning and push protection** on the repo (Settings → Code security) — free for public repos, and the safety net for the fact that nothing here can ever be private.

### 3. Generate the required secrets

```bash
# TOKEN_ENCRYPTION_KEY — encrypts the Spotify refresh token at rest
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# SESSION_SECRET_KEY — signs the admin session cookie
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

### 4. Set up Hostinger

**Confirmed via a real, working deploy on this VPS.** This specific VPS already has a separate Traefik project deployed (`network_mode: host`, real Let's Encrypt via the HTTP-01 challenge, Docker-socket-based service discovery with `exposedbydefault=false`) — likely set up previously for a different, sibling project sharing the same VPS, not something Hostinger's Docker Manager provides out of the box (per [Hostinger's own docs](https://www.hostinger.com/support/12040815-how-to-deploy-your-first-container-with-hostinger-docker-manager/), the platform itself has no built-in reverse proxy/domain routing at all). Because that Traefik runs in host networking mode, it can reach any container on the box directly via the Docker socket — no shared network needs joining, no host port needs publishing. `docker-compose.yml`'s `caddy` service therefore carries `traefik.*` labels (`traefik.enable=true`, a `Host()` router rule, `entrypoints=websecure`, `tls.certresolver=letsencrypt`, and a `loadbalancer.server.port` pointing at Caddy's internal port 80) instead of any `ports:` mapping.

In Hostinger's Docker Manager, use **Compose from URL**, pointing at this repo's `docker-compose.yml` (the raw GitHub URL). Set these environment variables in Hostinger's panel (never commit real values — see `.env.example` for the full list with generation instructions):

| Variable | Value |
|---|---|
| `SPOTIFY_CLIENT_ID` | from step 1 |
| `SPOTIFY_CLIENT_SECRET` | from step 1 |
| `SPOTIFY_REDIRECT_URI` | the exact redirect URI you registered in step 1 |
| `TOKEN_ENCRYPTION_KEY` | generated in step 3 |
| `SESSION_SECRET_KEY` | generated in step 3 |
| `DOMAIN` | a real domain pointed at this VPS's IP, **or** `<project-name>.<vps-hostname>.hstgr.cloud` (e.g. `event-playlist.srv1234567.hstgr.cloud`) if you don't have a custom domain yet — see below for why the bare VPS hostname alone isn't a good choice |
| `SITE_ADDRESS` | `http://<same value as DOMAIN>` — Traefik terminates real TLS in front of this, so Caddy only ever serves plain HTTP internally |
| `SECURE_COOKIES` | `true` once you've confirmed `https://<DOMAIN>` actually works end-to-end (see [TLS mode](#tls-mode)) |

Click **Update** to build and start the stack, then visit `https://<DOMAIN>/request` to confirm it's live.

**On using Hostinger's auto-assigned VPS hostname without a custom domain**: don't just use the bare hostname (e.g. `srv1234567.hstgr.cloud`) — if this VPS ever hosts more than one Traefik-routed project (check via `docker ps -a` and `docker inspect <container> --format '{{json .Config.Labels}}' | grep traefik` for existing projects), each one needs something distinct in its `Host()` rule to avoid a routing conflict. The established, already-working pattern on a shared Hostinger VPS is a **per-project subdomain** of the auto-assigned hostname — e.g. `event-playlist.srv1234567.hstgr.cloud` — confirmed via Hostinger's own wildcard DNS resolving arbitrary subdomains of `*.hstgr.cloud` with no extra setup needed, and Traefik issuing a separate valid Let's Encrypt cert per subdomain with zero conflict. This needs no app or `docker-compose.yml` changes — the app doesn't care what `Host` header it's reached by, unlike a path-prefix scheme (`/event-playlist/...`), which this app can't support without real code changes since every template/redirect uses root-absolute paths (`/static/...`, `/request`, etc.).

**If a redeploy doesn't seem to pick up a code/config change**: first confirm `publish-images.yml` actually ran and pushed for the commit you expect (Actions tab) and that the GHCR packages are public — a private package fails the pull silently from Hostinger's side. If that's all fine and "Update" still isn't picking up new content, Hostinger's Compose-from-URL has also been observed re-writing the on-disk `docker-compose.yml` with stale content on "Update" (possibly resolved once to a specific commit at project creation, not re-resolving `main` fresh every time) — bypass the panel: SSH in, check the file directly (Hostinger stores it at `/docker/<project-name>/docker-compose.yml`), overwrite it with the current `main` version if it's stale, then run `docker compose -f /docker/<project-name>/docker-compose.yml --project-directory /docker/<project-name> pull && docker compose -f /docker/<project-name>/docker-compose.yml --project-directory /docker/<project-name> up -d` directly.

**Historical note**: earlier revisions of this project built `app`/`caddy` directly from a git-context `build:` pointing at this repo, since Hostinger never clones the surrounding repo. That only ever built once — `docker compose up` skips building a `build:`-context image that already exists locally, so every later "Update" click silently kept reusing that first image no matter how many commits landed on `main`, even though the click itself appeared to succeed. Switching to registry images (this section) fixed it, since a `pull` always fetches the current tag. See `CLAUDE.md` for the full incident writeup.

### TLS mode

**Confirmed: the pre-existing Traefik project on this VPS terminates real TLS via Let's Encrypt**, discovering this project purely through the `traefik.*` labels on `caddy` (no shared network or published port needed, since Traefik runs with `network_mode: host`). `SITE_ADDRESS` should be `http://<DOMAIN>` — Caddy never attempts its own certificate; it only contributes security headers (CSP, HSTS, etc.) to whatever Traefik forwards it.

Getting a real certificate issued requires `DOMAIN`'s DNS to actually resolve to this VPS's IP before Traefik's HTTP-01 challenge can succeed — Hostinger's own auto-assigned VPS hostname works for this if you don't have a custom domain yet. Once `https://<DOMAIN>` is confirmed working end-to-end in a browser, set `SECURE_COOKIES=true` and click Update again — leaving it `false` over working HTTPS just means cookies aren't marked `Secure`; leaving it `true` before HTTPS actually works means the browser silently refuses to set the cookie at all and login will appear broken.

### 5. First login

On first boot, the app seeds a single admin user (`admin`) with a random one-time password. **It is never logged in plaintext** — it's written to a file on the persistent data volume and logged only as a file path. Retrieve it:

```bash
docker compose exec app cat /data/initial_admin_password.txt
```

(over SSH to whatever host Hostinger's Docker product runs on, or via Hostinger's own container shell/exec feature if it offers one). Log in at `https://<your-domain>/admin/login` with username `admin` and that password — you'll be forced to set a new password immediately, after which the file is deleted automatically.

### 6. Connect Spotify

From the admin nav, go to **Spotify** and click **Connect Spotify**. You'll be sent to Spotify's consent screen; approve it, and you're returned to a page confirming the connection and its granted scopes.

### 7. Set the default playlist

Open the venue's target playlist in Spotify, copy its ID from the share link (`https://open.spotify.com/playlist/`**`THIS_PART`**`?si=...`, or the middle segment of a `spotify:playlist:THIS_PART` URI), and paste it into **Default playlist ID** on the **Config** page. While you're there, review the other settings (explicit-track filtering, how many tracks ahead requests get inserted, playlist repeat, poll interval) — all documented inline on that page.

Make sure the playback hardware is playing that exact playlist, in order, with **shuffle off** — "insert requests X tracks ahead of current" has no stable meaning otherwise (SPEC.md §1).

You're live. Point people at `https://<your-domain>/request` to request songs, and `/menu` for now-playing / status lookup / the full playlist.

## First use, after deployment

Once the stack is up (whether this is the very first deploy, or a fresh redeploy after recreating the Hostinger project — which resets all env vars back to blank, a scenario worth planning for), here's the checklist to get from "container is running" to "actually usable":

1. **Generate the two required secrets** (skip if they're already set and you didn't just recreate the project):
   ```bash
   python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # TOKEN_ENCRYPTION_KEY
   python3 -c "import secrets; print(secrets.token_urlsafe(32))"                                # SESSION_SECRET_KEY
   ```
   Set both in Hostinger's environment panel exactly as printed — no extra quotes or trailing whitespace. Leaving either blank crash-loops the app with `ValueError: Fernet key must be 32 url-safe base64-encoded bytes` (see [Troubleshooting](#troubleshooting-app-crash-looping-right-after-deploy) below).

2. **Retrieve the seeded admin password**:
   ```bash
   docker exec event-playlist-app-1 cat /data/initial_admin_password.txt
   ```
   (run over SSH on the Hostinger VPS, or via its container shell/exec feature if it offers one). This file is written once at first boot with `0600` permissions, never logged in plaintext, and deletes itself automatically once step 3 below is complete.

3. **Log in and set a real password**: go to `https://<DOMAIN>/admin/login`, username `admin`, the password from step 2 — you'll be forced to change it immediately.

4. **Connect Spotify**: from the admin nav, go to **Spotify** → **Connect Spotify**, approve the consent screen.

5. **Set the default playlist**: on the **Config** page, paste in the target playlist's ID (from its share link or URI — see [step 7](#7-set-the-default-playlist)), and review the other settings (insert-tracks-ahead, repeat mode, poll interval) while you're there.

You're live at that point — `/request` and `/menu` are ready for real attendees.

## Releasing a change

Deploys are **manual**, not automatic on merge — Hostinger's Compose-from-URL only re-fetches and rebuilds when you tell it to:

1. Open a PR, wait for the required `lint-test-scan` CI check to go green (branch protection blocks merging otherwise)
2. Merge to `main` — `publish-images.yml` builds and pushes new `app`/`caddy` images to GHCR automatically
3. In Hostinger's Docker Manager, click **Update** — this re-pulls `docker-compose.yml` and the current `:latest` image tags, and restarts the stack

**Confirmed:** Update preserves the named `app_data` volume — your SQLite database and any stored Spotify tokens survive every redeploy. Only the application code changes.

## Operational tasks

### Rotating the Spotify client secret

1. In the Spotify Developer Dashboard, regenerate the app's client secret
2. Update `SPOTIFY_CLIENT_SECRET` in Hostinger's environment panel
3. Click **Update** to restart the app with the new value

The existing stored refresh token should keep working across this rotation, but if the Spotify admin page starts reporting errors afterward, just reconnect via **Spotify → Connect Spotify** — re-authorizing takes under a minute.

### Backing up the SQLite database

The database is a single file on a named Docker volume with no built-in redundancy. There's no `sqlite3` CLI in the container image (it's `python:3.12-slim`, which doesn't include it) — use Python's own `sqlite3` module instead, which can safely back up a live database without stopping the app:

```bash
docker compose exec app python3 -c "
import sqlite3, datetime
src = sqlite3.connect('/data/app.db')
dest = sqlite3.connect(f'/data/backup-{datetime.datetime.now(datetime.timezone.utc):%Y%m%d-%H%M%S}.db')
src.backup(dest)
dest.close(); src.close()
print('backup written')
"
```

Then copy the resulting `/data/backup-*.db` file off the container to somewhere durable (e.g. `docker compose cp app:/data/backup-20260721-140000.db ./`) — leaving backup files on the same volume as the live database doesn't protect against losing the volume itself.

### Restoring from a backup

```bash
docker compose cp ./backup-20260721-140000.db app:/data/app.db
docker compose restart app
```

The app re-applies its migrations against whatever schema state the restored file is in — restoring an older backup is safe as long as it predates the current migration set (check `app/migrations/` for what's landed since the backup was taken).

### Changing the default playlist

Update **Default playlist ID** on the **Config** page (see [step 7](#7-set-the-default-playlist) above for how to find a playlist's ID) — takes effect on the next poll interval, no restart needed.

### Troubleshooting: app crash-looping right after deploy

If the `app` container starts and immediately exits with `ValueError: Fernet key must be 32 url-safe base64-encoded bytes` in its logs (`docker logs event-playlist-app-1` or Hostinger's log panel), `TOKEN_ENCRYPTION_KEY` is blank, truncated, or wasn't actually saved in Hostinger's environment panel — regenerate it (the command's in [step 3](#3-generate-the-required-secrets)) and re-set it exactly as printed, no extra quotes or trailing whitespace, then click **Update** again. `SESSION_SECRET_KEY` being blank causes a related but distinct failure — check both are actually set (`docker exec event-playlist-app-1 printenv TOKEN_ENCRYPTION_KEY` / `SESSION_SECRET_KEY` against the running container) rather than assuming the panel saved what you typed.

### Troubleshooting: site loads but returns an empty page

If a request returns `HTTP/1.1 200 OK` with `Content-Length: 0` and `Server: Caddy` (no `Content-Type`, no real HTML) — Caddy is not proxying to the app: the request's `Host` header doesn't match `SITE_ADDRESS`. Caddy matches on that header exactly, port included. Confirm what's actually configured on the *currently running* container (not what you think you set — a stale earlier container can make this confusing if a redeploy happened between checks):

```bash
docker exec event-playlist-caddy-1 printenv SITE_ADDRESS
docker exec event-playlist-caddy-1 wget -qO- http://127.0.0.1:2019/config/
```

The second command dumps Caddy's actual compiled routing rule (via its own admin API) — check the `"match":[{"host":[...]}]` value against the exact domain you're requesting through Traefik.

### Troubleshooting: Spotify won't connect, or every Spotify API call fails

**Spotify shows `redirect_uri: Not matching configuration`** when you click Connect Spotify: the Redirect URI registered in the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) (app → Settings → Redirect URIs) doesn't match `SPOTIFY_REDIRECT_URI` character-for-character — scheme, host, and path all have to match exactly. This is easy to hit after changing domains (e.g. switching to the [per-project subdomain](#4-set-up-hostinger)) and forgetting to update the Dashboard side too.

**Everything Spotify-related returns `403 Forbidden`** after connecting successfully (search results empty with a 500 in the app logs, the background poller logging repeated `403` errors for `/me/player/repeat`, etc.): a newly created Spotify app defaults to **Development Mode**, which restricts *all* API access to an explicit allowlist of up to 25 accounts — regardless of the scopes you approved on the consent screen. Add the Spotify account you connected with under the Dashboard's **User Management** section (app → Settings → User Management), then reconnect.

**Clicking "Reconnect Spotify" appears to do nothing**: if the admin's browser is already logged into Spotify with these scopes already granted, Spotify silently re-approves and redirects straight back with no visible screen at all (confirmed via a HAR capture — a 303 straight to the callback, zero rendered UI). `build_authorize_url` now sends `show_dialog=true` to force the consent screen to always render — if you're on an older deploy that predates this, redeploy the current `main`. Note this still can't force an *account switch*; Spotify's OAuth has no such prompt, so picking a different account requires logging out of Spotify in the browser first (also see the note in the admin Spotify page itself). Separately, `/admin/spotify/callback` being reached via a cross-site redirect from Spotify is also why the admin session cookie is `SameSite=Lax`, not `Strict` — a `Strict` cookie doesn't survive that redirect at all (see `CLAUDE.md`).

## Deployment model

Hosting is Hostinger's Docker product via **Compose-from-URL**: Hostinger fetches only `docker-compose.yml` itself, never the surrounding repo. `docker-compose.yml` references pre-built `image:` tags on GHCR — `.github/workflows/publish-images.yml` builds and pushes them on every merge to `main` — rather than building from a git context, since a `build:`-context image is only ever built once by `docker compose up` (see the historical note above for the incident that caused this switch). Both the repo and the GHCR packages have to be public, since neither Hostinger's YAML fetch nor its image pull can authenticate (mitigated by GitHub's secret scanning/push protection, step 2 above). See [`CLAUDE.md`](./CLAUDE.md) for the rest of the architecture.

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Or via Docker Compose (`docker-compose.override.yml` builds from the local tree instead of pulling the GHCR image Hostinger uses):

```bash
cp .env.example .env
docker compose up --build
```

## Tests

```bash
pytest
```

## Lint / format / security checks

```bash
ruff check .
black --check .
pip-audit -r requirements.txt
bandit -r app -ll

# Malicious-dependency scan (informational — see .github/workflows/guarddog.yml
# for why this isn't gated on findings)
grep -h -v -E '^(-r |#|$)' requirements.txt requirements-dev.txt > /tmp/all-requirements.txt
guarddog pypi verify /tmp/all-requirements.txt --exclude-rules repository_integrity_mismatch
```
