---
name: run-app
description: Start the Spot the Spin app locally in stub mode (uv and uvicorn, or Docker Compose), smoke-test its endpoints and pages with the bundled script, and optionally run a read-only check against the public URL on the Hostinger VPS. Use automatically after implementing any API, frontend or deployment change, and whenever the user asks to run, start, open or check the app. Never deploys: deployment is a separate step the user confirms.
---

# run-app

The app is only done when it starts and answers. This skill proves that quickly, without spending tokens on exhaustive checks. One smoke pass is the default; more is for debugging.

## Modes

### 1. Local, stub mode (default, use for every implementation change)
Stub mode needs no API key and no network.

From the repo root:
```
CORE_MODE=stub uv run uvicorn app.main:app --app-dir backend --port 8000
```
Run it in the background so the shell stays free. Wait until `/api/health` answers (poll at most 20 times, one second apart). Then:
```
bash .claude/skills/run-app/scripts/smoke.sh http://127.0.0.1:8000
```
Stop the server when done. Report the smoke output in full.

If the app does not start, read the traceback, fix it in the owned files, and try once more. Do not change other agents' files; report them instead.

### 2. Local, Docker (infra changes only)
```
CORE_MODE=stub docker compose build
CORE_MODE=stub docker compose up -d
bash .claude/skills/run-app/scripts/smoke.sh http://127.0.0.1:8000
docker compose down
```
Compose publishes no ports in production, so for local checks use the override described in `docs/DEPLOY.md`, or run the container directly on a local port. Do not change the production compose file for this.

### 3. Public, read-only (only when the orchestrator or the user asks)
```
bash .claude/skills/run-app/scripts/smoke.sh https://srv2160832.hstgr.cloud
```
This only sends GET requests, plus one POST to `/api/analyze` with a short neutral sentence. It does not change anything on the server. Do not run any `ssh` command from this skill.

## What the smoke script checks
- `GET /api/health`: required, must return 200 with `"ok": true`. Failure exits non-zero.
- `GET /api/round`: returns three headlines (if the endpoint exists).
- `POST /api/analyze` with a short text: returns a verdict with `evidence_spans` (if the endpoint exists).
- `GET /api/metrics`: returns 200 with real data, or 404 if no real results exist yet. Both are acceptable; a 500 is a failure.
- `GET /`, `/analyze/`, `/metrics/`: return 200 and HTML (if the frontend exists).
- Any 5xx from any endpoint fails the run.

Missing endpoints are reported as `MISSING` and do not fail the run, so the same script works while the app is being built.

## Troubleshooting
- **Port in use:** pick another port, for example 8001, and pass the matching URL to the script.
- **Import errors:** run `uv sync` first; check that `--app-dir backend` is set.
- **Public URL shows the default Traefik certificate:** the router is not matched yet. Check the labels, then remember that Traefik must be restarted after any Docker daemon update: `docker restart traefik-traefik-1`. Those restarts are the server admin's call, so ask first.
- **Public URL returns 404 from Traefik:** the container is not running, or its labels do not match `Host(...)`.

## Rules
- Never print or log the AI Router key. Stub mode does not need it; live mode reads it from `.env`.
- Never run `docker compose down -v` or delete volumes: they hold the SQLite database.
- Never run the public check repeatedly in a loop.
