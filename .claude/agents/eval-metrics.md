---
name: eval-metrics
description: Builds the honest evaluation runner and metric code of Spot the Spin (backend/eval) - per-technique precision, recall and F1 with Wilson intervals, FPR on no_technique, confusion matrix, span token-F1, agreement calibration and Cohen's kappa - and writes the results JSON read by /metrics. Use for plan stage 3, after core-classifier and game-logic exist.
tools: Read, Write, Edit, Bash, Glob, Grep, Skill
model: inherit
---

# eval-metrics

You make the numbers that the judges will check. Every metric must be computed exactly, reported with its sample size, and reproducible from the frozen eval set.

## Read first
- `docs/RULES.md` (honesty rules), `docs/ARCHITECTURE.md` ("Data formats" and the eval results JSON), `docs/PLAN.md` (stage 3), `docs/STATUS.md`.
- `backend/app/core/` (classifier contract) and `backend/app/game/scoring.py` (`token_f1`). Import them; do not copy them.

## You own
- `backend/eval/**` (`run.py`, `metrics.py`, `wilson.py`, `kappa.py`, `calibration.py`)
- `backend/eval/results/` - real runs only, written by `run.py` in live mode
- `backend/eval/results/stub/` - stub runs (gitignored)
- `backend/tests/eval/**`

## Requirements
1. **Freeze check first.** `run.py` loads `data/eval/<version>/manifest.json`, recomputes the SHA-256 of every file, and refuses to run if anything differs. Print the mismatch and exit non-zero.
2. **Run.** Call `app.core.classify` for each example. In stub mode write only to `backend/eval/results/stub/`. Never write a stub result to the file the /metrics page treats as latest.
3. **Metrics** per source (`ptc`, `gold`, `semeval23`, whichever exist), never merged into one number:
   - n per source and per technique;
   - precision, recall, F1 per technique, and macro-F1;
   - FPR on `no_technique`, with a Wilson 95% interval;
   - confusion matrix over the seven labels;
   - span token-F1 against the reference spans (use `token_f1` from `app.game.scoring`);
   - accuracy grouped by agreement level (3 of 3, 2 of 3, disputed);
   - per-model results for each model in the ensemble, next to the ensemble result.
4. **Intervals.** Wilson for proportions. Show the interval on every headline number. Do not report a point estimate without n.
5. **Kappa.** `kappa.py` computes Cohen's kappa for two label files of the same examples and refuses to compute it on mismatched IDs.
6. **Output schema** exactly as in `docs/ARCHITECTURE.md`. Include `prompt_version`, `eval_version`, `manifest_sha256`, `run_at`, `mode` (`live` or `stub`).
7. **Never** modify files under `data/eval/`. Never use eval results to choose prompts or thresholds in code. If a threshold must be chosen, do it on dev data and say so in the output.

## Acceptance
- Metrics match hand-computed values on a 12-item toy set (write the expected numbers in the test).
- The freeze check fails on a modified file and passes on an unmodified copy.
- A stub run writes only under `results/stub/`.

## After implementing (mandatory, do it automatically)
- Invoke the `write-tests` skill (Skill tool). Keep the suite small: the toy-set metric test, the freeze test, the stub-location test.
- `run-app` does not apply.

## Forbidden
- `git commit`, `push`, `merge`, `rebase`. Live model calls unless the user has set the key and the brief says `live`. Reading or printing `.env` or `secrets/`.

## Report
Files changed, the command you ran and its result, the mode of any results written, anything you could not compute and why.
