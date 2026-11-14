# Proposed architecture (seed for docs/ARCHITECTURE.md)

A starting point, not a law. Once `docs/ARCHITECTURE.md` exists, it wins; change it deliberately and tell affected agents.

## Repo layout

```
junction_2026/
├── backend/
│   ├── app/
│   │   ├── core/          # the ONE classifier: prompt, model clients, aggregation, cache
│   │   ├── api/           # FastAPI routers: /api/analyze, /api/round, /api/answer, /api/dispute, /api/metrics
│   │   ├── game/          # round assembly, scoring (60 + 40 × token-F1)
│   │   ├── news/          # RSS fetch, event clustering, daily job (daily.py): label, store rounds, pick the daily challenge
│   │   ├── scheduler.py   # in-process 00:00 scheduler (DAILY_* settings), idempotent per date via job_runs
│   │   └── db.py          # SQLite: rounds, answers, disputes
│   ├── eval/              # eval runner + metric functions → results JSON
│   └── tests/
├── frontend/              # game, Analyze, /metrics (renders results JSON)
├── data/
│   ├── raw/               # downloaded corpora — never committed if the licence forbids it
│   ├── dev/               # few-shot + prompt tuning
│   └── eval/              # FROZEN test set, versioned (e.g. eval_v1/)
├── scripts/               # dataset conversion, gold-set labelling helpers
├── docs/                  # PLAN.md, STATUS.md, ARCHITECTURE.md
├── docker-compose.yml
└── .env.example           # names of secrets only; real .env is gitignored
```

## Core contract

Every consumer (game, Analyze, /metrics, news job) calls this and nothing else.

```python
TECHNIQUES = [
    "loaded_language", "appeal_to_fear", "exaggeration_minimization",
    "doubt", "vague_authority", "changing_subject", "no_technique",
]

@dataclass
class ModelVote:
    model: str                 # e.g. "claude", "gpt", "deepseek"
    technique: str             # one of TECHNIQUES
    evidence_spans: list[str]  # exact substrings of the input text
    explanation: str

@dataclass
class Verdict:
    technique: str             # majority vote; "no_technique" if no majority
    evidence_spans: list[str]  # spans chosen by >= 2 models
    explanation: str           # plain language, from a model that voted with the majority
    agreement: int             # how many models voted for `technique` (0..3)
    n_models: int              # usually 3
    disputed: bool             # True when agreement < 2 — not shown as a confident verdict
    votes: list[ModelVote]

def classify(text: str) -> Verdict: ...
```

Rules attached to the contract:
- `evidence_spans` must be exact substrings of `text` (validated; invalid spans are dropped, not invented).
- Results are cached by hash of normalized text + prompt version.
- Until the real core exists, `core/stub.py` returns deterministic fake verdicts with the same types, so consumers can be built in parallel.

## Deployment facts attached to the contract

- The app serves `GET /health` and `GET /api/health` (both 200 when the process is up); the container health check accepts either.
- Traefik on the VPS runs in host network mode, so the app compose needs no shared Docker network: labels only, like the hello-world.
- The image contains `backend/`, `frontend/` and `data/dev/` (few-shot at runtime). `data/eval/`, `data/raw/` and all secrets stay out of the image.

Round rule: a round is the 3 headlines of one event with the maximal number of distinct reference techniques (`no_technique` counts as one value), always including a clean headline when the event has one; an event with no clean headline qualifies only with 3 distinct techniques. Ready rounds store `diversity` (1-3) and `techniques` (sorted list, internal only, never returned by `GET /api/round`). Daily ranking: diversity, then novelty (techniques not used in the last 7 daily challenges), then outlets, then freshness; a day is never left empty (the most diverse available is set and `daily_diversity` is recorded in `job_runs`). The ensemble honestly labels most real news as clean, so variety depends on the news of the day; nothing is relabelled to force it. Settings: `DAILY_MAX_CLUSTERS` (8), `DAILY_MAX_PER_CLUSTER` (6), `DAILY_MAX_HEADLINES` (48). `python -m app.news.daily --rebuild-today` rebuilds today's unanswered rounds (paid models only for headlines missing from the cache).

SQLite tables (`app/db.py`): `rounds` (with `n_outlets`, `diversity`, `techniques`), `round_items`, `answers`, `disputes`, `dispute_votes`, `daily_challenge` (one round per local date), `job_runs` (one row per local date; the idempotency guard of the daily job).

## Game rules attached to the contract

- Tokenizer (`app.game.scoring.tokenize`, also used by eval for span token-F1 and by the game page for clickable words): lowercase, split on characters that are not letters or digits, except that a hyphen between two letters or digits stays inside the token ("rip-off" is one token). This keeps the PLAN worked example at 87.
- Headlines whose reference `Verdict.disputed` is True are not used in rounds; the API filters them out of the pool before `build_round`.
- Disputes enter review after N distinct voters (default 3) or by explicit team escalation (`escalate_by_team`). Rescored items go to dev only.

## Data formats

`data/dev/*.jsonl` and `data/eval/<version>/*.jsonl`, one example per line:

```json
{"id": "ptc-123-4", "source": "ptc|gold|semeval21|semeval23", "text": "...", "technique": "appeal_to_fear", "evidence_spans": ["drown", "gridlock"]}
```

Eval results JSON (written by `backend/eval`, read by the /metrics page) is keyed by source; each entry holds `n`, per-technique P/R/F1 with Wilson intervals, macro-F1, FPR on no_technique, confusion matrix, span token-F1, accuracy by agreement level, per-model results.
