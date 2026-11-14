# Status: Spot the Spin

_Updated: 2026-11-14 by the orchestrator_

## Stages

| # | Stage | Status | Notes |
|---|---|---|---|
| 0 | Infrastructure and deployment skeleton | in progress | VPS rebooted, public URL 200. Foundation, Dockerfile, compose (labels only, Traefik in host network mode), deploy scripts with `--confirm`, runbook committed. First app deploy waits for the API and the user's yes. Docker is not installed in WSL, so no local image build |
| 1 | Datasets and gold set | in progress | SemEval-2021 Task 6: dev 527 + multi 142 + few-shot 14 committed; **eval_v1 frozen** (135). Gold set of real headlines not started (needs two annotators). Few-shot needs a human review (5 weak picks of 14) |
| 2 | Core ensemble classifier | done | committed; prompt v3. Models verified 2026-11-14 via GET /models: gpt-5.5, claude-sonnet-5.5, deepseek-v4.1-flash. One live call on a dev example: 3 of 3, exact spans |
| 3 | /metrics page | in progress | eval runner: agent working; live eval run by the orchestrator after review; UI in mock mode: agent working |
| 4 | Game | in progress | logic committed; UI in mock mode: agent working; API next |
| 5 | Analyze mode | in progress | UI in mock mode: agent working; API next |
| 6 | Real news (RSS) | in progress | fetch + clustering + offline dry run committed; feeds not fetched live yet; DB integration waits for api-backend |
| 7 | Disputes and human vs model | todo | |
| 8 | Final | todo | |

Statuses: todo · in progress · done · blocked · cut

## Tasks in progress

frontend-builder (mock mode, phase B); eval-metrics (phase C). Next: api-backend (after the frontend's mock shapes), then news DB integration and invariant-guard.

## Needed from the human

- [ ] Review `data/dev/fewshot.jsonl` (14 examples; some are offensive meme texts or weak labels).
- [ ] Before the first app deploy: yes to stop the hello-world (same Host rule) and create `/opt/spotthespin/app/.env` (mode 600) on the server.
- [ ] Find a second annotator for the gold set (postponed).
- [ ] Later: Hermes port 32768 and passwordless sudo for `deploy` (postponed).

## Blockers

- None. PTC is no longer needed.

## Frozen test set

- `data/eval/eval_v1/` — SemEval-2021 Task 6 test split, 135 examples (41 no_technique, 68 loaded_language, 10 doubt, 8 exaggeration_minimization, 4 changing_subject, 2 appeal_to_fear, 2 vague_authority). `semeval21_test.jsonl` sha256 `1e07d6907c0e5f91ae26a29c7bbb42871aab5272fcfb5c143841102fd26da91d`. Frozen 2026-11-14 on the user's approval, commit `aa60f70`, before any prompt tuning. Suggested tag: `eval_v1`.

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
- 2026-11-14 — eval_v1 frozen from the SemEval-2021 Task 6 test split by user decision (small n and meme-domain shift accepted, shown on /metrics).
- 2026-11-14 — `vague_authority` broadened to "appeal to authority" (named or unnamed) by user decision, to match the SemEval label; id kept; prompt bumped to v3. Decided after the freeze, from dev examples only.
- 2026-11-14 — Live model calls approved by the user: model check and the eval_v1 run.
- 2026-11-14 — Game tokenizer keeps hyphenated words as one token (keeps the plan's worked example at 87). Disputed references are not used in rounds. Cache key includes a hash of the few-shot examples.
