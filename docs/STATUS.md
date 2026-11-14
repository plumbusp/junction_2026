# Status: Spot the Spin

_Updated: 2026-11-14 by the orchestrator_

## Stages

| # | Stage | Status | Notes |
|---|---|---|---|
| 0 | Infrastructure and deployment skeleton | in progress | VPS configured (SSH key login, firewall, Docker). Hello-world runs at https://srv2160832.hstgr.cloud. Remaining: application Dockerfile, reboot after updates |
| 1 | Datasets and gold set | todo | PTC registration not confirmed. `data/` does not exist yet |
| 2 | Core ensemble classifier | todo | no code in the repository yet |
| 3 | /metrics page | todo | |
| 4 | Game | todo | |
| 5 | Analyze mode | todo | |
| 6 | Real news (RSS) | todo | |
| 7 | Disputes and human vs model | todo | |
| 8 | Final | todo | |

Statuses: todo · in progress · done · blocked · cut

## Tasks in progress

None.

## Needed from the human

- [ ] Put `AI_ROUTER_API_KEY` into `.env` (never in chat). Base URL is `https://router.hostinger.com/v1`.
- [ ] Decide: reboot the VPS after updates (Hermes and Traefik will restart).
- [ ] Decide: close the public Hermes port 32768 or not.
- [ ] Decide: keep passwordless sudo for user `deploy`.
- [ ] Confirm PTC registration (propaganda.qcri.org/ptc/registration.php).
- [ ] Find a second annotator for the gold set.
- [ ] Decide whether to commit the current files (docs, .gitignore, .dockerignore, .claude/skills).

## Blockers

- PTC registration is not confirmed, so there is no dataset yet.

## Frozen test set

- None yet. `data/eval/` has not been created.

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
