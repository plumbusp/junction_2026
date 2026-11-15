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
    technique: str             # a technique only when all 3 models name it (ACCUSE_MIN_VOTES);
                               # "no_technique" when 2+ say clean, on a near-miss or with no majority
    evidence_spans: list[str]  # spans chosen by >= 2 models
    explanation: str           # plain language, from a model in the majority; on a near-miss
                               # it says "2 of 3 models saw <technique> ... not accused"
    agreement: int             # how many models voted for `technique` (0..3)
    n_models: int              # usually 3
    disputed: bool             # True on a near-miss or no majority — not shown as a confident verdict
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

Round rule: a round is the 3 headlines of one event with the maximal number of distinct reference techniques (`no_technique` counts as one value), always including a clean headline when the event has one; an event with no clean headline qualifies only with 3 distinct techniques. Ready rounds store `diversity` (1-3) and `techniques` (sorted list, internal only, never returned by `GET /api/round`). Daily ranking: diversity, then novelty (techniques not used in the last 7 daily challenges), then outlets, then freshness; a day is never left empty (the most diverse available is set and `daily_diversity` is recorded in `job_runs`). The ensemble honestly labels most real news as clean, so variety depends on the news of the day; nothing is relabelled to force it. Lineup round: because the ensemble labels ~96% of real news as clean, each run also labels a wider pool (`DAILY_LINEUP_POOL`, balanced across outlets) and builds one "lineup" round per day (cluster id `lineup-<local date>`, idempotent per date): three real headlines from three distinct outlets (and, where possible, three stories), always including a clean one, maximising distinct techniques, then distinct stories, novelty vs the last 7 dailies, distinct declared leanings, freshness. It competes in the daily ranking like any other round; `GET /api/round` is unchanged. Counts `pool_labelled` and `lineup_diversity` are stored in `job_runs`. **The lineup is OFF by default** (user decision, 2026-11-14: rounds are about one event again). Daily ranking is now: has_technique (at least one reference technique other than `no_technique`), diversity, novelty vs the last 7 dailies, outlets, freshness; if no ready round has a technique a daily is still set and `job_runs` counts `daily_has_technique: 0`. Settings: `DAILY_MAX_CLUSTERS` (12), `DAILY_MAX_PER_CLUSTER` (8), `DAILY_MAX_HEADLINES` (200, cap per run), `DAILY_LINEUP_POOL` (0 = off; up to 400). Feeds: 35 English outlets (`app/news/feeds.py`), declared leaning is an approximation of public media-bias ratings and describes the outlet, not the headline. `python -m app.news.daily --rebuild-today` rebuilds today's unanswered rounds (paid models only for headlines missing from the cache).

SQLite tables (`app/db.py`): `rounds` (with `n_outlets`, `diversity`, `techniques`), `round_items`, `answers`, `disputes`, `dispute_votes`, `daily_challenge` (one round per local date), `job_runs` (one row per local date; the idempotency guard of the daily job).

## Prompt examples (retrieval few-shot, prompt v4)

Each prompt carries the 14 fixed anchors from `data/dev/fewshot.jsonl` plus the `RETRIEVAL_K` (4) nearest labelled dev examples for that text (`app/core/retrieval.py`: TF-IDF over word unigrams and bigrams, cosine, news-headline boost 0.08, deterministic ties, an example equal to the input is skipped). Dev only: the index refuses anything under `data/eval/`. `FEWSHOT_MODE=static` restores anchors only. The cache key hashes the examples actually used. `backend/eval/dev_check.py` compares modes by leave-one-out on `data/dev/headlines_dev.jsonl` (dev-only results, not an eval). Endpoints added: `GET /api/stats/disputes` (counts only). `POST /api/analyze` has a global daily cap (`ANALYZE_DAILY_CAP`).

## `GET /api/round` contract (one challenge per local day)

- **Open round:** `{round_id, date, event, source_note, items:[{item_id,text,outlet}]}` plus `daily: true` for today's daily. No references or techniques are revealed. Partial progress continues the same round; answering an item twice is 409.
- **Completed:** HTTP 200 `{completed: true, daily, date, next_edition_at (ISO-8601 with offset), round_id, round_score, items:[{item_id,text,outlet,player_technique,reference_technique,total}]}`. Returned once the player answered every item of today's daily; no other round is ever served afterwards. `reference_technique` only for items the player already answered. If there is no daily for today, the old fallback (newest unfinished round, `daily: false`) applies.
- **503** `no_rounds` when nothing is available. The old 409 `all_rounds_played` is gone.
- The next edition is the next `DAILY_JOB_HOUR:DAILY_JOB_MINUTE` in `DAILY_TZ`. Manual refresh: `python -m app.news.daily --rebuild-today` inside the container.
- Known gap: after a reload mid-round the page restarts at clipping 1 and skips already-answered clippings via 409; an additive `answered_item_ids` field would fix it cleanly.

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
