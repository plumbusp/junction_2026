# Spot the Spin — junction_2026

Project for the **Junction 2026** hackathon, **Hostinger** challenge: "Build an AI-powered tool that detects propaganda, bots and bad actors online, and deploy it live on Hostinger's infrastructure."

Our direction is **Name the technique**: a classifier that finds manipulation techniques in news headlines and explains each verdict in plain language. Around it we build the game **Spot the Spin**, where players learn to see manipulation, and the correct answer, score and system confidence are shown at every step.

> **Status.** The current state lives in [docs/STATUS.md](docs/STATUS.md). In short: infrastructure (stage 0) is ready, the VPS is configured, and a test hello-world runs over HTTPS. The application code is not written yet; agents from `.claude/agents/`, orchestrated by the repo-orchestrator skill, will build it.

---

## Contents

1. [What we are building](#1-what-we-are-building)
2. [Jury criteria and what follows from them](#2-jury-criteria-and-what-follows-from-them)
3. [Product](#3-product)
4. [Six manipulation techniques](#4-six-manipulation-techniques)
5. [Architecture](#5-architecture)
6. [Repository layout](#6-repository-layout)
7. [Plan stages and status](#7-plan-stages-and-status)
8. [Data and metric honesty](#8-data-and-metric-honesty)
9. [Local setup](#9-local-setup)
10. [Tests and evaluation](#10-tests-and-evaluation)
11. [Deployment on Hostinger](#11-deployment-on-hostinger)
12. [Environment variables](#12-environment-variables)
13. [Agents and skills](#13-agents-and-skills)
14. [How the orchestrator builds the application](#14-how-the-orchestrator-builds-the-application)
15. [Git and working rules](#15-git-and-working-rules)
16. [Project documentation](#16-project-documentation)
17. [Common problems](#17-common-problems)
18. [License](#18-license)

---

## 1. What we are building

Three related parts that share **one core** (the classifier):

| Part | What it does | Audience |
|---|---|---|
| **Spot the Spin game** | a round of three headlines about one event: the player picks a technique, marks evidence words, sees the reference answer, score and system confidence, and can dispute the answer | players, jury demo |
| **Analyze** | paste text or a URL, get highlighted fragments, the technique and a plain-language explanation | any user, public API |
| **/metrics** | precision, recall, F1 per technique, false-positive rate on "no technique", confusion matrix, confidence calibration, sample sizes and intervals | jury: proof of honesty |

Plus two supporting sources: **real news of the day** (RSS from several outlets with different leanings, grouped by event) and a **dispute loop** (a player disagrees with the model; the dispute is reviewed before it touches any data).

Why: the jury prioritises **honest, measurable detection** (precision, false-positive rate, handling of false positives) over interface polish. So one classifier powers all three parts, and metrics are computed on a frozen test set that was never used for prompt tuning.

---

## 2. Jury criteria and what follows from them

From the challenge brief:

- The tool **must run on Hostinger** and be reachable at a public URL that the judges open themselves.
- Judges value **the strength and honesty of the detection logic**, including how false positives are handled.
- Approaches that are **transparent and measurable** are especially interesting: show precision and false-positive rate, not just a demo on happy-path examples.
- Resources available: VPS KVM 2, AI Router with 150+ models behind one key, OpenAI- and Anthropic-compatible API.

Rules we derive from this:

1. Live deployment first (stage 0), then everything else. The public URL is not left to the last night.
2. Metrics are a separate page with sample sizes and intervals. Without it, the main argument disappears.
3. Three different models instead of three runs of one model: confidence = share of agreeing models.
4. Anything that cannot be measured honestly is listed as a limitation, not hidden.

Hostinger resources from the brief: [docs.hostinger.com/hostinger-connector](https://docs.hostinger.com/hostinger-connector), [developers.hostinger.com](https://developers.hostinger.com), [github.com/hostinger/api-mcp-server](https://github.com/hostinger/api-mcp-server).

---

## 3. Product

### 3.1 Game: the five steps of a round

1. **Headlines.** Three headlines about one event. Usually one of them is "clean" (no technique), so players do not see manipulation everywhere.
2. **Technique.** For each headline the player picks one of six techniques or "no technique".
3. **Evidence.** The player clicks the words that support the choice.
4. **Answer.** The reference technique, the reference words, the score and the system confidence are shown (for example "3 of 3" or "disputed case").
5. **Dispute.** If the player disagrees, they pick their own option and send the dispute for review.

### 3.2 Scoring

For each headline:

- **Technique**: 60 points if it matches the reference, otherwise 0. Technique points do not depend on the words.
- **Evidence words**: `40 × token-F1` between the player's words and the reference words. Rounded to the nearest integer, halves up. Evidence is scored independently of the technique choice.
- **Special case:** the reference is "no technique" and the player selected no words: full 40 points.

Round total: the mean of the three headlines, rounded to the nearest integer.

Example from the team's presentation: A = 60 + 27 = **87**; B = 0 + 27 = **27**; C = 60 + 40 = **100**; round = round((87 + 27 + 100) / 3) = **71**. Here 27 = 40 × 0.667, where the player chose "slams" and the reference is "slams" and "rip-off" (P = 1, R = 0.5).

This example is encoded in the tests and must not change.

### 3.3 System confidence

The system runs three different models. Confidence is the **share of agreeing models**, not a probability. That is why we show "3 of 3" or "2 of 3", never "100% correct". When there is no agreement, the headline is marked as **disputed** and is not presented as a confident verdict.

### 3.4 Analyze

Input: text or a URL (only `https`; internal network addresses are refused; size is capped). Output: the technique, highlighted fragments with positions in the text, the explanation, model agreement and the `disputed` flag. Requests are rate-limited per IP.

### 3.5 /metrics

The page reads the results of the last **real** run on the frozen test set. It shows:

- n per source and per technique;
- precision, recall, F1 per technique, and macro-F1;
- **FPR on "no technique"** with a 95% Wilson interval;
- the confusion matrix;
- quality of the evidence words (token-F1);
- accuracy by agreement level (3 of 3, 2 of 3, disputed);
- results of each model separately and of the ensemble;
- Cohen's kappa between two annotators (the human agreement ceiling).

If the results come from stub mode, the page shows a warning. Nothing is hidden.

### 3.6 News of the day and disputes

- **News.** RSS from 8–15 English-language outlets with different leanings. Headlines are grouped by event within a 48-hour window; a cluster becomes a round only if it contains at least three outlets. Candidates go to labelling first, not straight into the game.
- **Disputes.** Flow: `queued → in_review → rescored`. One vote changes nothing: a dispute goes to review after N independent disputes of the same case. Disputed cases go only into the **dev** set, never into the test set.

---

## 4. Six manipulation techniques

Our taxonomy (from the team's presentation) and its mapping to the PTC corpus labels (SemEval-2020 Task 11):

| Technique (id) | What it is | PTC labels |
|---|---|---|
| Loaded language and labels (`loaded_language`) | emotionally charged words, labels | Loaded_Language, Name_Calling, Labeling |
| Appeal to fear (`appeal_to_fear`) | pressure through fear | Appeal_to_fear-prejudice |
| Exaggeration or minimization (`exaggeration_minimization`) | overstating or understating | Exaggeration, Minimisation |
| Doubt (`doubt`) | hints of doubt without evidence | Doubt |
| Vague authority (`vague_authority`) | reference to unnamed "experts" | Appeal_to_Authority |
| Changing the subject (`changing_subject`) | deflection from the question | Whataboutism, Straw_Men, Red_Herring |
| No technique (`no_technique`) | ordinary information, no manipulation | — |

PTC labels outside our six (for example Flag-Waving, Slogans, Repetition) are **excluded** from evaluation, not turned into `no_technique`. Otherwise the model would be penalised for a correct finding.

---

## 5. Architecture

```
            ┌────────────────────────── browser ──────────────────────────┐
            │  game  ·  Analyze  ·  /metrics  ·  labelling (offline)       │
            └──────────────┬───────────────────────────────────────────────┘
                           │ HTTPS
                  ┌────────▼────────┐
                  │ Traefik (Hostinger, already on 80/443, Let's Encrypt)
                  └────────┬────────┘
                           │ Docker network (no ports published to the host)
            ┌──────────────▼──────────────────────────────────────────┐
            │  FastAPI  (backend/app)                                  │
            │  api/  ── round · answer · dispute · analyze · metrics   │
            │  game/ ── scoring (60 + 40×F1) · rounds · disputes       │
            │  news/ ── RSS · clusters · daily job                     │
            │  core/ ── THE ONLY classifier ◄── all parts use it       │
            │  db.py ── SQLite (rounds, answers, disputes)             │
            │  security/ ── SSRF protection, rate limit                │
            └──────┬───────────────────────────────┬──────────────────┘
                   │ classify(text) -> Verdict      │ reads results
          ┌────────▼─────────┐            ┌────────▼────────┐
          │ Hostinger AI      │            │ backend/eval     │
          │ Router            │            │ metrics, runner  │
          │ 3 different models│            │ → results JSON   │
          └──────────────────┘            └─────────────────┘
                                                  ▲
                                 data/eval (FROZEN) · data/dev
```

### 5.1 Classifier core

Contract (details in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)):

- `classify(text) -> Verdict`
- `Verdict`: `technique`, `evidence_spans` (exact substrings of the input), `explanation`, `agreement` (0..3), `n_models`, `disputed`, `votes`.
- Three models through AI Router, called in parallel, 20 s timeout, one retry on error. A failed model is excluded; if fewer than two successful votes remain, the verdict is disputed.
- Spans that do not occur in the text are dropped. Nothing is invented.
- Cache keyed by a hash of (prompt version, normalised text, model list).
- `CORE_MODE=stub` returns deterministic answers without keys. All tests and parallel development run on the stub.

### 5.2 Stack

| Layer | Tool | Status |
|---|---|---|
| Server | Hostinger VPS KVM 2, Ubuntu 24.04 | configured |
| Proxy and HTTPS | Traefik (already on the server, Let's Encrypt) | running |
| Containers | Docker 29, Compose | running |
| Backend | Python 3.12, FastAPI, uvicorn, httpx, Pydantic | planned |
| Storage | SQLite | planned |
| Models | Hostinger AI Router (`https://router.hostinger.com/v1`) | key received |
| Tests | pytest | planned |
| Packages | uv | required |
| Frontend | HTML, CSS, JavaScript without a build step | planned |
| News | feedparser, TF-IDF | planned |
| Text extraction from URLs | trafilatura | planned |

Details: [docs/TOOLS.md](docs/TOOLS.md).

---

## 6. Repository layout

Legend: ✅ exists, 🔜 will be created by an agent.

```
junction_2026/
├── .claude/
│   ├── agents/                  🔜 low-level build agents (section 13)
│   └── skills/
│       ├── repo-orchestrator/   ✅ project orchestrator
│       ├── write-tests/         ✅ tests after implementation
│       └── run-app/             ✅ run and smoke check
├── backend/
│   ├── app/
│   │   ├── config.py            🔜 settings from .env (key is never printed)
│   │   ├── main.py              🔜 FastAPI application
│   │   ├── db.py                🔜 SQLite
│   │   ├── core/                🔜 THE ONLY classifier
│   │   ├── game/                🔜 scoring, rounds, disputes
│   │   ├── news/                🔜 RSS, clusters, daily job
│   │   ├── api/                 🔜 endpoints
│   │   └── security/            🔜 SSRF, rate limit
│   ├── eval/                    🔜 metrics, runner, results/
│   └── tests/                   🔜 pytest
├── frontend/                    🔜 game, Analyze, /metrics, labelling
├── data/                        🔜 (see section 8; created by agents)
│   ├── raw/                     corpora, NOT committed
│   ├── dev/                     few-shot examples and prompt tuning
│   ├── eval/                    FROZEN test set
│   ├── news/                    RSS candidates, NOT committed
│   └── cache/                   classifier cache, NOT committed
├── scripts/                     🔜 converters, news job
├── deploy/                      🔜 helper scripts (never run automatically)
├── docs/                        ✅ plans, status, rules, knowledge base, infrastructure
├── Dockerfile                   🔜
├── docker-compose.yml           🔜
├── .dockerignore                ✅
├── .gitignore                   ✅
├── .env.example                 ✅ variable names only
├── .env                         ✅ local, NOT in git
├── pyproject.toml               🔜
├── LICENSE                      ✅
└── README.md                    ✅ this file
```

---

## 7. Plan stages and status

The full plan is in [docs/PLAN.md](docs/PLAN.md). Summary with done-criteria below.

| # | Stage | Status | Done when |
|---|---|---|---|
| 0 | Infrastructure and deployment skeleton | in progress | VPS configured; hello-world over HTTPS; application Dockerfile and runbook ready |
| 1 | Datasets and gold set | not started | PTC obtained and converted; gold set of 150–200 real headlines labelled by two people; eval frozen with a manifest |
| 2 | Core ensemble classifier | not started | `classify` works in live and stub modes; aggregation and span tests green; models verified through `/v1/models` |
| 3 | /metrics page | not started | eval runner writes JSON; page shows P/R/F1, FPR, matrix, intervals, kappa |
| 4 | Game | not started | five-step round works; scores match the 87/27/100/71 example |
| 5 | Analyze mode and public API | not started | `/api/analyze` returns exact spans; SSRF protection verified |
| 6 | Real news of the day | not started | RSS candidates with clusters of 3+ outlets; round of the day is assembled |
| 7 | Disputes and human vs model | not started | a dispute passes all statuses; test metrics do not change |
| 8 | Final | not started | public URL verified; limits enabled; demo script rehearsed; slides updated |

**Cut order if time runs short:** stage 7 first, then 6 (keep pre-built rounds). Stages 0–3 and 5 are not cut.

**Review comments** (from the team's slide) that the plan addresses: no /metrics; three runs of one model are a weak signal; the game is not yet a tool; headlines are invented; disputes are unused; slides 2 and 7 have truncated text; the 27 on slide 5 is not explained.

---

## 8. Data and metric honesty

### 8.1 Sources

| Source | What | Status |
|---|---|---|
| PTC, SemEval-2020 Task 11 | news articles, span-level labels, 14 techniques | registration required: propaganda.qcri.org/ptc/registration.php. The user places files in `data/raw/ptc/` |
| SemEval-2023 Task 3, subtask 3 | English news paragraphs, ~19 techniques | registration open. Second independent set |
| SemEval-2021 Task 6 | meme texts, 22 techniques | availability not verified |
| Own gold set | 150–200 real headlines from RSS, labelled by two people | not created |

Headlines generated by an LLM are allowed only as few-shot examples and for demos. They never enter evaluation.

### 8.2 Rules

1. **The test set is frozen** (`data/eval/eval_vN/` with `manifest.json` and SHA-256). It is never changed, and never used for few-shot examples, prompts or thresholds. A change means a new version and a decision by the user.
2. **Dev and test do not overlap.** `scripts/data/check_splits.py` verifies this on normalised text.
3. **Player disputes** go only into dev, and only after review.
4. **Honest metrics:** always n, Wilson intervals, FPR on "no technique", sources are never merged into one number.
5. **Raw corpora are not committed**; licences are checked before use.

These rules are enforced not only in code but also by agents: `invariant-guard` checks them before every commit (section 13).

### 8.3 What already happened

While building the orchestrator skill, an invented `eval_v1.json` with made-up metrics appeared in test fixtures. Those fixtures were deleted, and the results were kept outside the repository. The repository itself **does not have a frozen set yet**. This is recorded in [docs/STATUS.md](docs/STATUS.md).

---

## 9. Local setup

### 9.1 Requirements

- Git 2.43 or newer, Python 3.12, uv. Commands below are written for bash.
- A local copy of the repository.
- A `.env` file in the repository root. Template: `.env.example`.

### 9.2 First run

```bash
cd <path-to-repository>
uv sync                                  # once pyproject.toml exists
CORE_MODE=stub uv run uvicorn app.main:app --app-dir backend --port 8000 --reload
```

In another terminal:

```bash
bash .claude/skills/run-app/scripts/smoke.sh http://127.0.0.1:8000
```

### 9.3 Modes

- **`CORE_MODE=stub`**: the classifier answers deterministically; no key and no network needed. Used for development, tests and demos without spending credits.
- **`CORE_MODE=live`**: real model calls through AI Router. Needs the key in `.env` and a verified `CORE_MODELS` list. Enabled deliberately.

To list the models (put the key into your shell, never into a chat):

```bash
curl -s -H "Authorization: Bearer $AI_ROUTER_API_KEY" https://router.hostinger.com/v1/models
```

This endpoint does not check the key, so a 200 response does not prove the key is valid. Confirm the key with a chat request.

Copy model IDs into `CORE_MODELS` only after they are verified.

---

## 10. Tests and evaluation

### 10.1 Tests

```bash
uv run pytest -q                    # all tests
uv run pytest backend/tests/game -q # one module
```

Principles: few tests, each meaningful. For each function: a happy path and one failure case; plus invariant tests (no eval leakage into the prompt, spans are exact substrings, the 87/27/100/71 example still holds, the key does not appear in output). Tests make no network calls and call no models. The `write-tests` skill describes this in detail.

### 10.2 Evaluation

The evaluation runs on the frozen set and writes the JSON results that `/metrics` reads:

```bash
uv run python -m eval.run --eval-version eval_v1 --mode live   # only with a key and an approved set
uv run python -m eval.run --eval-version eval_v1 --mode stub   # writes only to results/stub/
```

Before running, the runner verifies the SHA-256 of every file in the set. If a file changed, it refuses to run.

These commands describe the planned interface; they exist once the eval-metrics agent has built them.

---

## 11. Deployment on Hostinger

### 11.1 What is already on the server

- Host `srv2160832.hstgr.cloud`, IP `31.220.55.73`, Ubuntu 24.04, 2 vCPU, 7.8 GB RAM.
- User `deploy` (SSH key login, passwordless sudo, docker group). Root login only by key. Password login disabled.
- ufw firewall: ports 22, 80, 443 and 32768 open.
- **Traefik** (from Hostinger) owns ports 80 and 443, issues Let's Encrypt certificates and routes by container labels.
- **Hermes Agent** (Hostinger template) runs on port 32768 and is left untouched.
- **hello-world** (`/opt/spotthespin/hello`) answers at https://srv2160832.hstgr.cloud.

Details: [docs/INFRA.md](docs/INFRA.md).

### 11.2 How a service connects to Traefik

Our container **publishes no ports**. Traefik finds it through labels:

```yaml
labels:
  - traefik.enable=true
  - traefik.http.routers.<name>.rule=Host(`srv2160832.hstgr.cloud`)
  - traefik.http.routers.<name>.entrypoints=websecure
  - traefik.http.routers.<name>.tls.certresolver=letsencrypt
  - traefik.http.services.<name>.loadbalancer.server.port=8000
```

The working example is `/opt/spotthespin/hello/docker-compose.yml` on the server.

**Important:** after updating or restarting the Docker daemon, restart Traefik: `docker restart traefik-traefik-1`. Otherwise Traefik loses the Docker socket and stops seeing labels. This already happened on 2026-11-14.

### 11.3 Deployment order

Deployment happens **only after an explicit confirmation from the user**. Agents never deploy.

1. The `infra-deployer` agent prepares `Dockerfile`, `docker-compose.yml`, `.dockerignore` and `docs/DEPLOY.md`.
2. Locally: `docker compose config`, build, smoke test (`run-app` skill, mode 2).
3. The user confirms.
4. On the server (following the runbook in `docs/DEPLOY.md`): update the code, create `/opt/spotthespin/app/.env` by hand with permissions `600`, run `docker compose build && docker compose up -d`.
5. Public check (read-only): `run-app` skill, mode 3.
6. Rollback: previous image, described in the runbook.

The server's `.env` is **never copied** into the repository and never ends up in the image (see `.dockerignore`).

### 11.4 Hermes on port 32768

The port is published to the internet. ufw does not filter published Docker ports, so the ufw rule does not close it. It can only be closed by changing the port publication in Hermes's configuration. That is the user's decision.

---

## 12. Environment variables

Secret values are never written into the repository, the documentation or the chat.

| Variable | Example | Secret | Where to get it |
|---|---|---|---|
| `AI_ROUTER_BASE_URL` | `https://router.hostinger.com/v1` | no | [Hostinger, AI Router section](https://www.hostinger.com/support/?p=19146) |
| `AI_ROUTER_API_KEY` | `sk-hstgr-…` | **yes** | Hostinger panel → AI Router |
| `CORE_MODE` | `stub` or `live` | no | default `stub` |
| `CORE_MODELS` | comma-separated IDs | no | verify with `GET /v1/models` |
| `DB_PATH` | `data/app.db` | no | default |
| `VPS_HOST` | `31.220.55.73` | no (do not publish needlessly) | Hostinger panel |
| `VPS_USER` | `deploy` | no | our user on the server |
| `VPS_SSH_KEY_PATH` | `~/.ssh/id_ed25519` | **yes** (file) | local key on the personal machine; its public part is already on the server |
| `PUBLIC_DOMAIN` | `srv2160832.hstgr.cloud` | no | Hostinger panel |

Rules:
- `.env` and `secrets/` are excluded in `.gitignore` and `.dockerignore`.
- The key is read only through `app.config` and never appears in `repr` or logs.
- On the server, `.env` is created by hand with permissions `600`.
- If a key leaks, reissue it in the Hostinger panel. Deleting the line is not enough.

---

## 13. Agents and skills

### 13.1 Build agents (`.claude/agents/`)

Each agent owns its own paths. There are no overlaps, so they can run in parallel.

| Agent | Stage | Owns | Phase | Skills |
|---|---|---|---|---|
| `foundation-scaffolder` | 0–2 (foundation) | `pyproject.toml`, `backend/app/config.py`, `backend/app/core/types.py`, foundation tests | A | write-tests |
| `core-classifier` | 2 | `backend/app/core/` (except types) | B | write-tests |
| `data-curator` | 1 | `data/dev`, `data/eval` (with approval), `scripts/data/` | B | write-tests |
| `game-logic` | 4 (logic) | `backend/app/game/` | B | write-tests |
| `news-pipeline` | 6 | `backend/app/news/`, `scripts/news/` | B → D | write-tests, run-app (dry run) |
| `frontend-builder` | 3–5 (UI) | `frontend/` | B | run-app (mandatory) |
| `infra-deployer` | 0, 8 | `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `deploy/`, `docs/DEPLOY.md` | B | write-tests, run-app (local only) |
| `api-backend` | 4–5, 8 | `backend/app/main.py`, `api/`, `db.py`, `security/` | C | write-tests, run-app (mandatory) |
| `eval-metrics` | 3 | `backend/eval/` | C | write-tests |
| `invariant-guard` | gate | nothing (read-only) | D and before every commit | — |

Each agent definition contains:
- what to read before starting;
- what it owns and what it must not touch;
- requirements and acceptance criteria;
- **a mandatory step after implementation**: invoke the `write-tests` skill and, where listed, `run-app`;
- prohibitions: no commit, push, merge or rebase; no deployment; no reading of `.env` or `secrets/`.

### 13.2 Skills (`.claude/skills/`)

| Skill | When it is invoked | What it does |
|---|---|---|
| `write-tests` | automatically after a module is implemented | writes a minimal pytest set: acceptance criteria, one failure case per function, invariant tests; no network, no live models |
| `run-app` | automatically after an API, frontend or infrastructure change | starts the app in stub mode, runs `scripts/smoke.sh`, stops it; public check only on request, read-only |
| `repo-orchestrator` | when you ask "what's next", "launch the agents" | keeps the status, dispatches work to agents, verifies results, asks you where the decision is yours |

Skills are deliberately short and do not generate extra tests for themselves: one smoke run where applicable.

### 13.3 `invariant-guard`

This is a read-only agent; it changes nothing. It checks:
1. The test set has not changed.
2. No text from eval appears in the prompt or in dev.
3. Dev and eval do not overlap.
4. No secrets in files (`sk-hstgr-`, private keys, passwords); `.env` and `secrets/` are git-ignored.
5. No live model calls in tests.
6. Every changed file belongs to exactly one agent.
7. Agents made no commits.
8. Stub results are not presented as real ones.
9. There is only one classifier core.
10. No script contacts the server without confirmation.
11. No Cyrillic in repository files (chat with the user may be in Russian; repository content is English).

Verdict: `OK` or `BLOCK` with evidence (`path:line`). No commit is proposed until the verdict is `OK`.

---

## 14. How the orchestrator builds the application

The orchestrator (`.claude/skills/repo-orchestrator/SKILL.md`) runs the cycle **look → decide → dispatch → verify → report**. It writes little code itself and dispatches work to agents by phase.

| Phase | Who | Why |
|---|---|---|
| **A** | `foundation-scaffolder` (alone) | everything else depends on `pyproject.toml`, settings and contract types |
| **B** (parallel) | `core-classifier`, `data-curator`, `game-logic`, `news-pipeline` (pure parts), `frontend-builder` (mock mode), `infra-deployer` (local only) | no file overlaps; depend only on phase A |
| **C** (parallel) | `api-backend` (needs core and game), `eval-metrics` (needs core, game and an approved set) | depend on phase B results |
| **D** | `news-pipeline` (database integration after `db.py`), then `invariant-guard` over the whole batch | check before any commit |

Orchestrator rules:
- A brief for an agent is self-contained: goal, owned files, contracts, acceptance criteria, report format.
- An agent's report is not taken on trust: the orchestrator runs `uv run pytest -q` and `smoke.sh` itself.
- Agents are not asked to deploy, freeze a set or make live model calls without your explicit yes in this conversation.
- Commit and push happen only after your explicit approval and an `OK` verdict from `invariant-guard`.

---

## 15. Git and working rules

### 15.1 Git

- Plain `git` from the repository root, version 2.43 or newer.
- Git identity is already set in `.git/config`. Do not change it.
- Branch `main`. Agents do not commit; their changes stay in the working tree.
- Commit with explicit paths (`git add <path>`), never `git add -A`.
- **Commit, push, merge and deployment happen only after an explicit "yes" in the conversation.** Approval of one commit does not carry over to the next.

### 15.2 Project rules

The full list is in [docs/RULES.md](docs/RULES.md). Summary:
1. The test set is frozen.
2. Dev and test do not overlap.
3. Synthetic data never enters evaluation.
4. Disputes go only to dev, and only after review.
5. Metrics are honest: n, intervals, FPR, no merging of sources.
6. One classifier core.
7. Secrets only in `.env` and `secrets/`.
8. Deployment only with confirmation.

### 15.3 Language

- Everything in the repository is written in **English**: code, identifiers, comments, documentation (including this README), agent and skill definitions, test cases, fixtures, commit messages.
- Russian is used only in the chat with the user.

---

## 16. Project documentation

| File | Contents | Who changes it |
|---|---|---|
| [docs/PLAN.md](docs/PLAN.md) | stages 0–8, datasets, evaluation rules | the user |
| [docs/STATUS.md](docs/STATUS.md) | stage status, tasks, blockers, decision log | the orchestrator after every cycle |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | modules, core contract, data formats | by agreement |
| [docs/KNOWLEDGE_BASE.md](docs/KNOWLEDGE_BASE.md) | challenge, jury criteria, techniques, datasets, decisions | as the project learns |
| [docs/RULES.md](docs/RULES.md) | repository rules: git, secrets, metrics, deployment, language | the team |
| [docs/TOOLS.md](docs/TOOLS.md) | stack tools and their status | as things change |
| [docs/INFRA.md](docs/INFRA.md) | non-secret facts about the server, Traefik, Hermes | as things change |
| `docs/DEPLOY.md` | deployment runbook | `infra-deployer` (to be created) |

---

## 17. Common problems

**The public address shows the `TRAEFIK DEFAULT CERT` certificate.** Traefik did not find a route or has not issued a certificate yet. Check the container labels and restart Traefik: `docker restart traefik-traefik-1`. The server administrator performs the restart, with your approval.

**The public address returns 404 from Traefik.** The container is not running, or the `Host(...)` label does not match the domain.

**`smoke.sh` prints "Is this the Spot the Spin app?".** The server is not ours, or `/api/health` does not return `{"ok": true}`. The script deliberately stops early in that case: a server may answer 200 on any path.

**`uv` cannot find the project.** Run it from the repository root and make sure `pyproject.toml` exists (created in phase A).

**The `git` command is not found.** Install Git 2.43 or newer and open a new terminal. Git working rules are in section 15.

**After a Docker update on the server, services disappeared from routing.** Restart Traefik (see section 11.2).

**The AI Router key does not work.** Check that it starts with `sk-hstgr-`, that `AI_ROUTER_BASE_URL` is `https://router.hostinger.com/v1`, and that `CORE_MODE` is `live`. In `stub` mode no key is needed at all.

---

## 18. License

See [LICENSE](LICENSE).
