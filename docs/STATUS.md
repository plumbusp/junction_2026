# Status: Spot the Spin

_Updated: 2026-11-14 by the orchestrator_

## Stages

| # | Stage | Status | Notes |
|---|---|---|---|
| 0 | Infrastructure and deployment skeleton | in progress | VPS rebooted 2026-11-14, all containers back, public URL 200. Foundation committed (`397a424`). Dockerfile/compose/runbook: agent working |
| 1 | Datasets and gold set | in progress | PTC dropped (no registration). SemEval-2021 Task 6 downloaded to `data/raw/` (public, research use). Converter, dev set and eval candidate: agent working. Eval not frozen |
| 2 | Core ensemble classifier | in progress | stub + aggregation + client with mocked tests: agent working. Live calls not approved yet |
| 3 | /metrics page | in progress | UI in mock mode: agent working |
| 4 | Game | in progress | pure logic: agent working; UI in mock mode: agent working |
| 5 | Analyze mode | in progress | UI in mock mode: agent working |
| 6 | Real news (RSS) | in progress | fetch + clustering + dry run: agent working; DB integration waits for api-backend |
| 7 | Disputes and human vs model | todo | |
| 8 | Final | todo | |

Statuses: todo · in progress · done · blocked · cut

## Tasks in progress

Phase B, parallel: core-classifier, game-logic, news-pipeline (pure parts), frontend-builder (mock mode), infra-deployer (local files), data-curator (SemEval-2021 Task 6).

## Needed from the human

- [ ] Approve freezing `eval_v1` from the SemEval-2021 Task 6 test split (after the candidate is reviewed).
- [ ] Approve live model calls through AI Router (key is in `.env`).
- [ ] Find a second annotator for the gold set (postponed).
- [ ] Later: Hermes port 32768 and passwordless sudo for `deploy` (postponed).

## Blockers

- None. PTC is no longer needed.

## Frozen test set

- None yet. Candidate in `data/staging/eval_v1_candidate/` (gitignored) until the user approves the freeze.

## Decision log

- 2026-11-14 — Ensemble of three models through AI Router. Confidence = share of agreeing models. Reason: three runs of one model give a weak signal.
- 2026-11-14 — The game, Analyze and /metrics use one core.
- 2026-11-14 — Secrets only in `.env` and `secrets/`, excluded in `.gitignore` and `.dockerignore`. They are not copied to production from the repository.
- 2026-11-14 — The test set is frozen before prompt tuning.
- 2026-11-14 — Test fixtures of the orchestrator skill (`evals/*/files/`) were removed: they contained invented code and a fake `eval_v1.json`. Run results are kept outside the repository, in `c:\Users\super\junction\repo-orchestrator-workspace`.
- 2026-11-14 — VPS configured: key login, user `deploy`, password login disabled, ufw enabled (22/80/443 + 32768 for Hermes). Caddy not installed: ports 80/443 are already used by Hostinger's Traefik.
- 2026-11-14 — Traefik restarted after a Docker update, because it lost the Docker socket and stopped seeing service labels. Rule: restart Traefik after every Docker update.
- 2026-11-14 — Hello-world deployed through Traefik: https://srv2160832.hstgr.cloud. No ports published to the host.
- 2026-11-14 — Repository language rule: everything in the repository is in English; Russian only in chat with the user.
- 2026-11-14 — VPS rebooted after updates by user decision; Traefik, Hermes and hello-world came back on their own.
- 2026-11-14 — PTC dropped: it needs registration and the authors forbid automatic download; unofficial mirrors are not used. Replaced by SemEval-2021 Task 6 (public repo by the authors, "free for general research use"). Mapping and multi-label rules in `docs/KNOWLEDGE_BASE.md`. Upstream test split = eval candidate; upstream train+dev = dev.
- 2026-11-14 — User allowed commits without push; every push still needs an explicit yes.
