# Event Playlist

A self-contained web app that lets venue attendees request songs, queues them for admin approval, and inserts approved tracks into a live Spotify playlist. See [`SPEC.md`](./SPEC.md) for the full spec-driven build plan and [`CLAUDE.md`](./CLAUDE.md) for an architecture summary.

This repository is currently at **Phase 7** of the build plan (SPEC.md §11) — the full app (scaffold through the background worker) plus a security hardening pass: CSRF protection on admin forms, login lockout, a Content-Security-Policy and other security headers, a read-only container root filesystem, and Dependabot. See [`CLAUDE.md`](./CLAUDE.md) for what's implemented so far.

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
