# Deployment runbook

Written for: whoever deploys Spot the Spin to the Hostinger VPS. Agents prepare these files; they never deploy.

**Deployment, redeployment and rollback happen only after an explicit "yes" from the user in the conversation** (`docs/RULES.md`, section 4). Approval of one deployment does not carry over to the next.

## How it works

```
browser --https--> Traefik (traefik-traefik-1, network mode "host", ports 80/443, Let's Encrypt)
                     |  container IP on the default project network, matched by labels: Host(`PUBLIC_DOMAIN`)
                     v
                   spotthespin-app-1 (uvicorn app.main:app, port 8000, not published)
                     |
                   volume spotthespin_app-data -> /app/var/app.db (SQLite)
```

Traefik runs with network mode `host`, so it reaches containers on any local bridge network. Like the hello-world, our compose file has no `networks:` section and no `traefik.docker.network` label; the app sits on the default network `spotthespin_default`.

The SQLite volume is mounted at `/app/var`, not `/app/data`: `data/dev/` (few-shot examples the core loads at runtime) is baked into each image under `/app/data/dev`, and a volume over `/app/data` would hide newer versions of it.

| File | Role |
|---|---|
| `Dockerfile` | multi-stage: uv installs locked deps into `/opt/venv`; runtime is `python:3.12-slim`, user `app` (uid 10001), port 8000, stdlib health check on `/health` or `/api/health` |
| `.dockerignore` | allowlist: only `pyproject.toml`, `uv.lock`, `backend/` (without tests), `frontend/` and `data/dev/` enter the build; `.env*`, `secrets/`, keys, `data/raw`, `data/news`, the frozen `data/eval`, `.git`, `docs` never do |
| `docker-compose.yml` | service `app`, Traefik labels, **no `ports:`**, named volume, env file from the server |
| `deploy/deploy.sh` | ships a committed git ref, builds on the VPS, switches the container. Dry run unless `--confirm` |
| `deploy/rollback.sh` | switches back to an earlier image. Dry run unless `--confirm` |
| `deploy/compose.local.yml`, `deploy/local.env` | local Docker check only (publishes `127.0.0.1:8000`, stub mode) |

Compose variables (set in the server `.env`, which is also passed with `--env-file`; defaults in brackets):

| Variable | Default | Meaning |
|---|---|---|
| `PUBLIC_DOMAIN` | `srv2160832.hstgr.cloud` | `Host()` rule of the router |
| `APP_ENV_FILE` | `/opt/spotthespin/app/.env` | the app's environment file |
| `APP_IMAGE` / `APP_TAG` | `spotthespin-app` / `latest` | image; `deploy.sh` sets `APP_TAG` to the git sha |

Server layout:

```
/opt/spotthespin/app/
  .env                  created by hand, chmod 600, never from the repo
  releases/<sha>/       source of each deployed commit (git archive)
  CURRENT               sha that is running
  PREVIOUS              sha that ran before (rollback target)
  backups/              SQLite backups
```

## One-time setup (before the first deploy)

Do these on the VPS as user `deploy`, after the user confirms. None of them is done by an agent.

1. **Labels match the working hello-world** (checked on 2026-11-14): `/opt/spotthespin/hello/docker-compose.yml` uses `traefik.enable=true`, `Host(\`srv2160832.hstgr.cloud\`)`, entrypoint `websecure`, `tls.certresolver=letsencrypt` and `loadbalancer.server.port`, with no `networks:` section and no `traefik.docker.network` label. Traefik runs with network mode `host`. Our compose file follows the same pattern; no network has to be created.
2. **Stop the hello-world first (user-confirmed step).** The hello-world router `hello` uses the same `Host(srv2160832.hstgr.cloud)` rule as our router `spotthespin`. With two routers on the same rule Traefik picks one unpredictably, so the hello-world must be stopped before the first app deploy, after the user confirms it: `cd /opt/spotthespin/hello && docker compose down` (it holds no data). The alternative is to give one of them another hostname.
3. **Create the env file:**
   ```
   mkdir -p /opt/spotthespin/app/releases /opt/spotthespin/app/backups
   touch /opt/spotthespin/app/.env && chmod 600 /opt/spotthespin/app/.env
   nano /opt/spotthespin/app/.env
   ```
   Variable names (values are typed on the server, never pasted into chat or the repo): `CORE_MODE`, `CORE_MODELS`, `AI_ROUTER_BASE_URL`, `AI_ROUTER_API_KEY`, `PUBLIC_DOMAIN`. `DB_PATH` is fixed by the compose file (`/app/var/app.db`); do not set it here.

   Daily challenge variables (all optional except `DAILY_JOB_ENABLED=true` to switch it on): `DAILY_JOB_ENABLED` (default false), `DAILY_JOB_HOUR` / `DAILY_JOB_MINUTE` (default 0 / 0), `DAILY_TZ` (default `Europe/Helsinki`, a guess; set the team's zone), `DAILY_MAX_CLUSTERS` (5), `DAILY_MAX_HEADLINES` (24, the paid cap: headlines x 3 models per day), `DAILY_TZ_FALLBACK_OFFSET_MINUTES` (120, used only if tz data is missing; the image installs `tzdata`).
4. **SSH from WSL.** `deploy.sh` runs in WSL and uses `ssh deploy@srv2160832.hstgr.cloud`. If the key is not in WSL's `~/.ssh` or agent, pass it: `DEPLOY_SSH_OPTS="-i /path/to/key"`. The key itself stays on the personal machine.

## Before every deploy

- Tests are green: `uv run pytest -q` (from WSL; `deploy.sh` runs it again).
- Eval results are up to date (`backend/eval`), and `/metrics` shows real numbers or an honest 404.
- The code to deploy is **committed**. `deploy.sh` ships `git archive <ref>`, so uncommitted files and the untracked `.env` / `secrets/` never leave the machine. It also refuses to ship if a secret-looking file is committed.
- The local Docker check passed, if Docker is available (see the last section).
- First deploy only: the hello-world is stopped (one-time setup, step 2), after the user confirmed it.
- The user said "yes" to this deployment.

## First deploy and redeploy

Same command for both. From WSL in the repo root:

```
bash deploy/deploy.sh                 # dry run: read the plan
bash deploy/deploy.sh --confirm       # execute, only after the user's "yes"
bash deploy/deploy.sh --ref <sha> --confirm   # deploy a specific commit
```

What it does: runs the tests, checks the archive for secrets, checks that `/opt/spotthespin/app/.env` exists with mode 600, uploads the commit to `releases/<sha>`, builds `spotthespin-app:<sha>` on the VPS, starts it with `docker compose up -d --no-build`, records `CURRENT`/`PREVIOUS`, and waits for the container to become healthy.

Before a redeploy that changes the database schema, take a backup:

```
docker exec spotthespin-app-1 python -c "import sqlite3; sqlite3.connect('/app/var/app.db').backup(sqlite3.connect('/app/var/backup.db'))"
docker cp spotthespin-app-1:/app/var/backup.db /opt/spotthespin/app/backups/app-$(date +%F-%H%M).db
```

## Daily challenge

With `DAILY_JOB_ENABLED=true` the app starts an in-process scheduler (single uvicorn worker). Every day at `DAILY_JOB_HOUR:DAILY_JOB_MINUTE` in `DAILY_TZ` it fetches the RSS feeds, clusters events, labels the best clusters with the live ensemble, stores them as rounds and designates one as that date's challenge. It runs once per local date (`job_runs` table), also across restarts; if the container starts after the scheduled time with no run for today, it catches up once in the background. A failed day does not break `/api/round`: it falls back to the newest unfinished round.

`scripts/` is not in the image. Run it by hand inside the container:

```
docker exec spotthespin-app-1 python -m app.news.daily            # respects "already done today"
docker exec spotthespin-app-1 python -m app.news.daily --force    # re-run for today (reuses cached verdicts)
docker exec spotthespin-app-1 python -m app.news.daily --rebuild-today   # rebuild today's unanswered rounds and re-pick the daily
```

`--rebuild-today` calls the paid models for headlines that are not in the cache. Rounds that already have answers or disputes are kept. If `DAILY_MAX_CLUSTERS`, `DAILY_MAX_PER_CLUSTER` or `DAILY_MAX_HEADLINES` are pinned in the server `.env`, they override the defaults (8 / 6 / 200). `DAILY_LINEUP_POOL` (default 120, 0 disables the mixed "lineup" round) sets how many extra headlines per run are labelled for it. A run can label up to `DAILY_MAX_HEADLINES` headlines (the paid step); the verdict cache keeps re-runs free.

The paid verdict cache lives in the volume (`/app/var/cache`, linked from `/app/data/cache`), so redeploys do not re-pay for labelled headlines. Check the run: `docker exec spotthespin-app-1 python -c "import sqlite3;print(sqlite3.connect('/app/var/app.db').execute('select * from job_runs order by 1 desc limit 3').fetchall())"` (counts only, no headline text).

## Rollback

```
bash deploy/rollback.sh                    # dry run, target = PREVIOUS
bash deploy/rollback.sh --confirm          # after the user's "yes"
bash deploy/rollback.sh --to <sha> --confirm
```

It reuses the image `spotthespin-app:<sha>` and `releases/<sha>` that are still on the server; nothing is rebuilt. The SQLite volume is not touched, so if the newer version migrated the schema, restore the matching file from `backups/` (stop the container first).

List what can be rolled back to: `docker images spotthespin-app` and `ls /opt/spotthespin/app/releases`.

## After deploying (read-only checks)

1. `docker ps --filter name=spotthespin-app-1` shows `(healthy)`.
2. From the local machine: `bash .claude/skills/run-app/scripts/smoke.sh https://srv2160832.hstgr.cloud`. It only sends GET requests and one neutral POST to `/api/analyze`.
3. The browser shows a valid Let's Encrypt certificate, not `TRAEFIK DEFAULT CERT`.
4. `/health` (or `/api/health`) answers 200 on the public URL.

## Traefik shows no route

Symptoms: 404 `page not found` from Traefik, or the `TRAEFIK DEFAULT CERT` certificate.

1. **Was Docker updated or the VPS rebooted?** Then Traefik has lost the Docker socket and no longer sees labels. Restart it (server admin's call, ask first):
   ```
   docker restart traefik-traefik-1
   ```
   **Rule: after any Docker daemon update or VPS reboot, run `docker restart traefik-traefik-1`.** This happened on 2026-11-14.
2. Is the container running and healthy? `docker ps -a --filter name=spotthespin`, `docker logs --tail 100 spotthespin-app-1`. Traefik ignores unhealthy containers.
3. Does the container have an IP? `docker inspect spotthespin-app-1 --format '{{json .NetworkSettings.Networks}}'` should show `spotthespin_default` with an address. Traefik (network mode `host`) reaches that address directly; do not add a `traefik.docker.network` label or an external network.
4. Do the labels match? `docker inspect spotthespin-app-1 --format '{{json .Config.Labels}}'`: `Host()` equals the domain, entrypoint `websecure`, resolver `letsencrypt`, port 8000.
5. Is the hello-world still running with the same `Host()`? Stop it (step 2 of the one-time setup).
6. Traefik's own view: `docker logs --tail 100 traefik-traefik-1`.

Never edit `/docker/traefik` or Traefik's compose file to fix our routing.

## Safety rules

- Never run `docker compose down -v` and never delete the `spotthespin_app-data` volume: it holds the SQLite database.
- Never run `docker compose config` with the production env file in a shared terminal or log: it prints every value from `env_file`, including the API key. Use `--no-interpolate` or the local env (`--env-file deploy/local.env`).
- Never copy `.env` or `secrets/` to the server from the repo; the server `.env` is typed by hand.
- Never publish the app port on the server; Traefik is the only way in.
- If a key leaks, reissue it in the Hostinger panel (`docs/RULES.md`, section 2).

## Housekeeping

Old images and releases pile up. Keep the last three, after checking `CURRENT` and `PREVIOUS`:

```
docker images spotthespin-app
docker image rm spotthespin-app:<old-sha>
rm -rf /opt/spotthespin/app/releases/<old-sha>
```

## Local Docker check (no server)

From the repo root, with Docker available:

```
docker compose --env-file deploy/local.env config            # renders labels, no ports in the base file
docker compose --env-file deploy/local.env -f docker-compose.yml -f deploy/compose.local.yml up --build -d
bash .claude/skills/run-app/scripts/smoke.sh http://127.0.0.1:8000
docker compose --env-file deploy/local.env -f docker-compose.yml -f deploy/compose.local.yml down
```

`deploy/local.env` points the app at `.env.example` (names only) and the override forces `CORE_MODE=stub`, so no key is needed and none is printed. The override is the only place a port is published, and only on `127.0.0.1`.
