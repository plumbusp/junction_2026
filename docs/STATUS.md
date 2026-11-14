# Status: Spot the Spin

_Updated: 2026-11-14 by the orchestrator_

## Stages

| # | Stage | Status | Notes |
|---|---|---|---|
| 0 | Infrastructure and deployment skeleton | done | **Live: https://srv2160832.hstgr.cloud** (commit `7cb361f`, deployed 2026-11-14, container healthy, Let's Encrypt via Traefik). Hello-world stopped. Server `.env` (mode 600) created from five variables of the local `.env` plus `CORE_MODE=live`, `DAILY_JOB_ENABLED=true`. Docker is not installed in WSL; images are built on the VPS |
| 1 | Datasets and gold set | in progress | SemEval-2021 Task 6: dev 527 + multi 142 + few-shot 14 committed; **eval_v1 frozen** (135). Gold set of real headlines not started (needs two annotators). Few-shot needs a human review (5 weak picks of 14) |
| 2 | Core ensemble classifier | done | committed; prompt v3. Models verified 2026-11-14 via GET /models: gpt-5.5, claude-sonnet-5.5, deepseek-v4.1-flash. One live call on a dev example: 3 of 3, exact spans |
| 3 | /metrics page | done | live eval_v1 run committed (`33ae24c`); page redesigned as a case file; per-model details only inside "Methodology" |
| 4 | Game | done (local) | logic + API + redesigned UI (cork board, one-screen examine, radio music); product name "no i am not brainwashed" |
| 5 | Analyze mode | done (local) | `POST /api/analyze` with SSRF guard and rate limit; page restyled |
| 6 | Real news (RSS) | in progress | fetch, cluster, label (live), store rounds, daily challenge with in-process scheduler at 00:00 (`DAILY_TZ`, default Europe/Helsinki) and `job_runs` idempotency. 12 feeds verified by plain GETs on 2026-11-14. **First live run on the server (catch-up at startup, 2026-11-14 19:05 UTC):** 475 items, 339 in window, 12 candidate clusters, 5 used, 21 headlines labelled by the live ensemble, 5 rounds ready, daily challenge set; 0 failed feeds. Next scheduled run: 00:00 Europe/Helsinki |
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
- 2026-11-14 — User approved push, deploy and live news collection. Product renamed to "no i am not brainwashed". Daily challenge at 00:00 every day; timezone default Europe/Helsinki is the orchestrator's guess, configurable via `DAILY_TZ`.
- 2026-11-14 — Feeds: HuffPost (empty feed) and WSJ (frozen) dropped, Slate added (declared leaning is an AllSides-style approximation, to be reviewed). New DB objects: `rounds.n_outlets`, `daily_challenge`, `job_runs`.
- 2026-11-14 — UI no longer shows "3 of 3 models" wording: confidence is High / Moderate / Disputed; the ensemble description and model IDs live only in /metrics "Methodology".
- 2026-11-14 — Deployed `7cb361f` to the VPS on the user's explicit request (push, deploy, live news collection). Hello-world stopped (same Host rule). Runbook deviation, by the user's request: the server `.env` was created from the local `.env` (five variable names only, piped over SSH, values never printed) instead of typed by hand.
- 2026-11-14 — Daily rounds now maximise distinct techniques (no repeated technique inside one challenge when the pool allows; novelty vs the last 7 dailies). Reason: the first live daily had three `no_technique` headlines.
- 2026-11-14 — Measured on production: the cheap ensemble labels ~96% of real news headlines `no_technique` (50 of 52 cached verdicts), so single-event rounds had no variety. Decision (orchestrator, to meet the user's "different techniques, no repeats"): the daily challenge may be a "lineup" of three real headlines from three outlets/stories, built from a wider labelled pool (120 headlines per run). First live run on the server: lineup diversity 3 (appeal_to_fear, loaded_language, no_technique). The prompt was NOT made bolder: that would trade variety for false positives and require a new eval.
- 2026-11-14 — Models switched to cheaper ones by user decision, chosen by price and family, not by eval results: `claude-haiku-5.5`, `gpt-5.4-nano`, `deepseek-v4.1-flash` (replacing gpt-5.5, claude-sonnet-5.5; deepseek kept). One eval_v1 run, no iteration: accuracy 0.607 [0.52-0.69], macro-F1 0.334, FPR on no_technique 7/41 = 17% [9-31], recall of technique detection at 2-of-3 = 67% (was 79%). The previous expensive-model run (accuracy 0.556, FPR 29%) stays in git history.
- 2026-11-14 — invariant-guard gate: OK. The English-only rule does not apply to upstream corpus records: a few SemEval meme texts contain Cyrillic (including one frozen eval record, which must not be edited). The per-example predictions file stays in git but is excluded from the Docker image.
- 2026-11-14 — Verdict cache moved into the Docker volume (`/app/var/cache`, symlinked from `/app/data/cache`) so the paid cache survives redeploys; image installs `tzdata`.
- 2026-11-14 — Game tokenizer keeps hyphenated words as one token (keeps the plan's worked example at 87). Disputed references are not used in rounds. Cache key includes a hash of the few-shot examples.
