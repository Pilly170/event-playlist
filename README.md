# Event Playlist

A self-contained web app that lets venue attendees request songs, queues them for admin approval, and inserts approved tracks into a live Spotify playlist a configurable number of tracks ahead of whatever's currently playing. A background worker removes requested tracks once they've played and keeps the playlist looping via Spotify's own repeat mode, so it never runs dry.

See [`SPEC.md`](./SPEC.md) for the full spec-driven build plan and [`CLAUDE.md`](./CLAUDE.md) for an architecture summary aimed at whoever (human or AI) picks up development next.

This repository has completed all 8 phases of the build plan (SPEC.md §11): scaffold, Spotify OAuth, admin auth/config, the public request form and menu, the admin approval workflow, the background worker, a security hardening pass, and this polish pass.

## What you need before you start

- A Spotify account with **Premium** (required for playback-state and repeat-mode control) that's logged into the venue's playback hardware as a Spotify Connect device
- A [Spotify Developer](https://developer.spotify.com/dashboard) account, to register an app and get a Client ID/Secret
- A Hostinger account with the Docker hosting product (SPEC.md §9 — Compose-from-URL) — Docker Manager has no built-in reverse proxy/domain routing, so this app is reached via a fixed port rather than a bare domain unless you deploy your own reverse proxy in front; see [step 4](#4-set-up-hostinger)
- A **public** GitHub repository containing this code (required — see [Deployment model](#deployment-model) below for why)

## First deploy, from scratch

### 1. Register a Spotify app

Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard), create an app, and note its **Client ID** and **Client Secret**. Add a **Redirect URI** matching exactly:

```
http://<your-domain-or-vps-hostname>:8443/admin/spotify/callback
```

(`http://localhost:8000/admin/spotify/callback` for local development only — see [step 4](#4-set-up-hostinger) for why production uses a fixed `:8443` rather than a bare domain, unless you've set up your own reverse proxy with real TLS in front).

### 2. Get this code onto a public GitHub repo

Hostinger's Compose-from-URL deploy path fetches only `docker-compose.yml` directly from GitHub — it never clones the surrounding repo, so the repo has to be public for the build to authenticate at all (see [Deployment model](#deployment-model)). Push this repo there if you haven't already, then edit `docker-compose.yml`'s `app.build.context` to point at your repo's git URL instead of the placeholder.

Enable **GitHub secret scanning and push protection** on the repo (Settings → Code security) — free for public repos, and the safety net for the fact that nothing here can ever be private.

### 3. Generate the required secrets

```bash
# TOKEN_ENCRYPTION_KEY — encrypts the Spotify refresh token at rest
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# SESSION_SECRET_KEY — signs the admin session cookie
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

### 4. Set up Hostinger

**Confirmed via a real, working deploy on this VPS.** Per [Hostinger's own docs](https://www.hostinger.com/support/12040815-how-to-deploy-your-first-container-with-hostinger-docker-manager/), Docker Manager has **no built-in reverse proxy or domain routing at all** — the platform's own intended way to reach any deployed project is `http://<vps-ip-or-hostname>:<published-port>`. There's a separate, pre-existing `traefik` process on host ports 80/443 on this specific VPS — that's unrelated to Hostinger's platform and unrelated to this project (most likely set up previously for a different, sibling project sharing the same VPS) — so `caddy` can't use 80/443 either way.

`docker-compose.yml`'s `caddy` service therefore publishes a **fixed** host port, `8443` (confirmed free via `sudo ss -tlnp` on the actual VPS before choosing it — see CLAUDE.md if this ever needs to change). It deliberately does *not* let Docker auto-assign a random port: that was tried first, but an unstable port breaks Spotify's OAuth redirect URI (which must exactly match a fixed, registered value) and gives attendees no stable URL across redeploys.

In Hostinger's Docker Manager, use **Compose from URL**, pointing at this repo's `docker-compose.yml` (the raw GitHub URL). Set these environment variables in Hostinger's panel (never commit real values — see `.env.example` for the full list with generation instructions):

| Variable | Value |
|---|---|
| `SPOTIFY_CLIENT_ID` | from step 1 |
| `SPOTIFY_CLIENT_SECRET` | from step 1 |
| `SPOTIFY_REDIRECT_URI` | the exact redirect URI you registered in step 1 — must include `:8443` |
| `TOKEN_ENCRYPTION_KEY` | generated in step 3 |
| `SESSION_SECRET_KEY` | generated in step 3 |
| `DOMAIN` | your VPS's hostname or IP (a real custom domain needs your own reverse proxy in front — see below) |
| `SITE_ADDRESS` | `http://<same value as DOMAIN>` — Caddy matches incoming requests' `Host` header against this exactly, so it must match what's actually in the URL bar, port included |
| `SECURE_COOKIES` | `false` — see [TLS mode](#tls-mode) below |

Click **Update** to build and start the stack, then visit `http://<DOMAIN>:8443/request` to confirm it's live. `docker-compose.yml`'s healthcheck will confirm the container is up even before that.

### TLS mode

**Confirmed: nothing currently terminates TLS in front of this app.** The `traefik` process on this VPS is unrelated to this project (see above) — there is no HTTPS path to this container at all right now. `SITE_ADDRESS` is plain `http://`, and `SECURE_COOKIES` must stay `false` (a `Secure` cookie is silently dropped by browsers over plain HTTP, and login will appear broken if this is set `true` without real HTTPS in place).

For a real venue deployment, you'll want your own domain with real TLS — that means deploying your own reverse proxy in front of this stack (Hostinger's docs describe both [Traefik](https://www.hostinger.com/support/connecting-multiple-docker-compose-projects-using-traefik-in-hostinger-docker-manager/) and [Nginx Proxy Manager](https://www.hostinger.com/support/how-to-set-up-nginx-proxy-manager-using-hostinger-docker-manager/) options), pointed at this container's fixed `8443` port. Once that's genuinely serving HTTPS end-to-end, update `DOMAIN`/`SITE_ADDRESS` to that real domain (`SITE_ADDRESS=http://<domain>` — your reverse proxy terminates TLS, Caddy still serves plain HTTP internally and only contributes its security headers) and set `SECURE_COOKIES=true`.

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

## Releasing a change

Deploys are **manual**, not automatic on merge — Hostinger's Compose-from-URL only re-fetches and rebuilds when you tell it to:

1. Open a PR, wait for the required `lint-test-scan` CI check to go green (branch protection blocks merging otherwise)
2. Merge to `main`
3. In Hostinger's Docker Manager, click **Update** — this re-pulls `docker-compose.yml`, rebuilds the `app` image from the current `main`, and restarts it

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

The second command dumps Caddy's actual compiled routing rule (via its own admin API) — check the `"match":[{"host":[...]}]` value against the exact host/port you're requesting. Then retest with a matching `Host` header, e.g. `curl -v -H "Host: <that value>" http://<vps-ip>:8443/request`.

## Deployment model

Hosting is Hostinger's Docker product via **Compose-from-URL**: Hostinger fetches only `docker-compose.yml` itself, never the surrounding repo, so `app.build.context` has to be a git URL rather than a local path — and since that build path can't authenticate against a private repo, the repo has to be public (mitigated by GitHub's secret scanning/push protection, step 2 above). See [`CLAUDE.md`](./CLAUDE.md) for the rest of the architecture.

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Or via Docker Compose (`docker-compose.override.yml` builds from the local tree instead of the git-context build Hostinger uses):

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
