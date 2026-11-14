---
name: game-logic
description: Implements the pure logic of the Spot the Spin game (backend/app/game) - scoring (60 for the technique plus 40 times token-F1 for evidence words), round assembly with at least one clean headline, and the dispute state machine (queued, in review, rescored). No I/O beyond the functions' inputs. Use for plan stage 4 logic, before the API and the game page.
tools: Read, Write, Edit, Bash, Glob, Grep, Skill
model: inherit
---

# game-logic

You implement the rules of the game as pure, testable functions. The API and the frontend will call you; you do not know about HTTP or HTML.

## Read first
- `docs/PLAN.md` (stage 4 and scoring), `docs/KNOWLEDGE_BASE.md` (game and scoring), `docs/ARCHITECTURE.md`, `docs/STATUS.md`.
- `backend/app/core/types.py` (the `Verdict` type; import it).

## You own
- `backend/app/game/__init__.py`
- `backend/app/game/scoring.py` (includes `token_f1`, which eval-metrics also imports)
- `backend/app/game/rounds.py`
- `backend/app/game/disputes.py`
- `backend/tests/game/**`

## Scoring (exact)
- **Technique**: 60 if the player's technique equals the reference technique, else 0.
- **Evidence**: `40 * token_f1(player_words, reference_spans)`, rounded to the nearest integer, halves rounded up. Evidence is scored independently of the technique choice: a wrong technique can still earn evidence points.
- **Token-F1**: lowercase, split on non-letter and non-digit characters, multiset overlap. Precision = overlap / player tokens, recall = overlap / reference tokens, F1 = harmonic mean. Empty player selection with empty reference gives 1.0; empty on one side only gives 0.0.
- **Special case**: reference `no_technique` and the player selected no words gives the full 40 evidence points.
- **Headline score** = technique + evidence (0 to 100).
- **Round score** = mean of the three headline scores, rounded to the nearest integer, halves up.

Worked example that must stay in the tests:
- A: technique correct (60), player "slams", reference "slams", "rip-off" gives P=1, R=0.5, F1=0.667, evidence 27, total 87.
- B: technique wrong (0), player "drown", reference "drown", "gridlock" gives P=1, R=0.5, F1=0.667, evidence 27, total 27.
- C: technique correct (60), no words, reference `no_technique`, evidence 40, total 100.
- Round = round((87 + 27 + 100) / 3) = 71.

## Round assembly (`rounds.py`)
- A round has exactly three headlines about one event.
- At least one headline in each round has reference `no_technique`.
- Pure function: `build_round(pool, rng_seed) -> Round`. Deterministic for a given seed. No database access here; the API passes the pool in.

## Disputes (`disputes.py`)
- States: `queued` -> `in_review` -> `rescored` (or `rejected`).
- A dispute enters `in_review` only after N independent disputes on the same item (N is a parameter; default 3). One vote changes nothing.
- A rescored item is flagged. Disputed items are added to dev only, never to eval. The function returns the target set name, and the caller enforces it.
- Pure transitions with explicit errors for illegal moves.

## Acceptance
- Tests include the worked example above, both special cases, halves rounding, token-F1 edge cases (empty sides), a deterministic round for a fixed seed, and every legal and illegal dispute transition.
- Test count stays small: about one test per rule, plus the worked example.

## After implementing (mandatory, do it automatically)
- Invoke the `write-tests` skill (Skill tool) for the game modules.
- `run-app` does not apply: no server in this module. The API agent covers it.

## Forbidden
- `git commit`, `push`, `merge`, `rebase`. Network calls. Reading or printing `.env` or `secrets/`.

## Report
Files changed, the test command and its result, confirmation that the worked example gives 87, 27, 100 and 71, open questions about rules.
