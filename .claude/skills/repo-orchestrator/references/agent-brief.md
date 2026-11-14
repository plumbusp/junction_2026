# Agent brief template

The agent starts with no memory of the conversation. Everything it needs goes in the brief. Keep it tight: an agent with a clear, bounded task does better than one with the whole plan pasted in.

```
## Goal
<one sentence: the single verifiable result>

## Context
<2–5 sentences: what the project is, where this piece fits, what already exists.
 Point to files to read first (docs/ARCHITECTURE.md, specific modules) instead of pasting them.>

## You own
<exact directories/files this agent may create or change. Everything else is read-only.>

## Contracts
<interfaces this piece must respect or provide — function signatures, JSON shapes, endpoints.
 If you depend on something not built yet, use the stub named here.>

## Rules
- Do not run git commit, push, merge or rebase, and do not deploy anything. Leave changes in the working tree.
- Never write secrets into files; read keys from environment variables named in .env.example.
- <relevant project invariants, e.g. "data/eval/ is read-only and must not be used for few-shot or tuning">

## Done when
<checkable acceptance criteria, including a command that proves it, e.g. `pytest backend/tests/test_scoring.py` passes>

## Report back
- files created/changed
- the command(s) you ran to verify and their result
- anything you could not do, and why
- open questions or assumptions you made
```

## Example

```
## Goal
Implement round scoring for the game: per-headline score = 60 × technique match + 40 × evidence token-F1; round score = mean of the headlines.

## Context
Spot the Spin is a news-manipulation game (see docs/PLAN.md, stage 4). The classifier contract is in docs/ARCHITECTURE.md (Verdict). Scoring is pure logic and must not call any model.

## You own
backend/app/game/scoring.py, backend/tests/test_scoring.py

## Contracts
score_headline(player_technique: str, player_words: list[str], ref: Verdict) -> int  (0..100)
score_round(scores: list[int]) -> int
Token-F1 over lowercased word tokens; if reference technique is "no_technique" and the player picked no words, evidence part = 40.

## Rules
- Do not run git commit, push, merge or rebase, and do not deploy anything.
- No model calls, no network.

## Done when
`pytest backend/tests/test_scoring.py` passes and covers: the slide example (slams vs slams+rip-off → 87), wrong technique (→ 27), clean headline with no words (→ 100), extra words lowering the score.

## Report back
files changed; pytest output; any ambiguity in the formula you had to resolve.
```
