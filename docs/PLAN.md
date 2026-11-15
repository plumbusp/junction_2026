# Spot the Spin — project plan (Junction 2026, Hostinger challenge)

Original plan from 14 November. Some decisions changed since (PTC is open on Zenodo and used in eval_v3; a text is accused only when all three models agree); the current state and every change are in [STATUS.md](STATUS.md).

## Context

Hostinger challenge: an AI tool that detects propaganda, bots or bad actors, **deployed live on Hostinger with a public URL**. Judges care most about **honest, measurable detection** — precision, false-positive rate, how false positives are handled — more than a polished front end. Our direction: **"Name the technique"** — classify manipulation techniques in text and explain the verdict in plain language.

Product: the game **Spot the Spin**. A round shows three headlines about one event. The player picks a technique (or "no technique"), clicks the evidence words, sees the reference answer, their score and the system's confidence, and can dispute the answer.

Key architectural idea: **the game, Analyze mode and /metrics share one core — the classifier.** The game is the showcase and a source of labels, not a separate product.

Techniques (6 + none): Loaded language and labels · Appeal to fear · Exaggeration or minimization · Doubt · Vague authority · Changing the subject · No technique.

## Stages (in priority order)

### Stage 0. Infrastructure and skeleton
- Hostinger VPS (KVM 2), Docker, free domain with HTTPS, Hostinger AI Router key. *(user-only: accounts, keys, domain)*
- Monorepo `junction_2026`: backend (Python/FastAPI) with classifier, metrics and API; frontend (game, Analyze, /metrics); SQLite for rounds, answers and disputes.
- Deploy a hello-world early and redeploy after every stage, so "live URL" is never left to the last night.

### Stage 1. Datasets and gold set (parallel with stage 2)
Output: `data/eval/` (frozen labelled test set), `data/dev/` (for prompt tuning), taxonomy mapping to our 6 techniques. Details in "Datasets" below.

### Stage 2. Core — ensemble classifier
- Three **different** models via AI Router (Claude, GPT, DeepSeek) instead of three runs of one model. Same prompt; each returns strict JSON: `technique`, `evidence_spans` (exact substrings of the input), `explanation` (1–2 plain sentences).
- Prompt: definitions of the 6 techniques plus "no technique"; few-shot examples from dev only; explicit "when in doubt — no technique".
- Aggregation: technique by majority vote; evidence = words chosen by ≥2 models; confidence = "3 of 3 / 2 of 3 / no agreement" (share of agreeing models, not a probability).
- False-positive policy: if models disagree, the headline is marked "disputed" and not presented as a confident verdict. /metrics shows the trade-off "flag at ≥2/3" vs "flag only at 3/3".
- Cache verdicts by text hash (cheaper, and stable for the game).

### Stage 3. /metrics page — the main argument for the judges
- An `eval` script runs the core on the frozen test set and writes a results JSON; the page renders that JSON (no live run per visit).
- Shows: per-technique precision / recall / F1 and macro-F1; **FPR on "no technique"**; confusion matrix; evidence quality (token-F1 of spans); **agreement calibration** (accuracy at 3/3 vs 2/3); single models vs ensemble; human ceiling (Cohen's κ between the two annotators); sample sizes and Wilson intervals.
- "Known limitations": domain shift (articles → headlines), possible leakage of public datasets into model training, techniques outside our taxonomy.

### Stage 4. Game — a five-step round
- Screens: headlines → technique pick → word clicks → answer → dispute.
- Scoring: technique 60 or 0; evidence **40 × token-F1** against the reference (e.g. player "slams", reference "slams", "rip-off": P=1, R=0.5, F1=0.67 → 27). If the reference is "no technique" and the player selected no words, full 40. Round score = mean of the three headlines.
- Every round includes at least one clean headline.

### Stage 5. Analyze mode
- Paste text or a URL (extract headline and lead, e.g. trafilatura) → same core → highlighted phrases, technique, explanation, model agreement.
- Public `POST /api/analyze`.

### Stage 6. Real news of the day
- RSS from 8–15 English outlets with different leanings; group headlines by event (embeddings + cosine clustering in a 24–48 h window); keep clusters with ≥3 distinct sources; label ahead of time (cron) → "round of the day".
- Fallback: a pre-built pool of rounds.

### Stage 7. Human vs model, dispute loop
- Dispute status flow: queued → in review → rescored. One vote changes nothing; review starts at N disagreements or by team decision.
- Stats: where players most often disagree with the model, per technique.
- Confirmed disputes become new labels in **dev only, never in the frozen test set**.

### Stage 8. Final
- Final deploy and smoke test of the public URL; rate limits on /api/analyze; demo script (round → /metrics → Analyze on a live article); update the slides.

**Cut order if needed:** 7 first, then 6 (keep pre-built rounds). Stages 0–3 and 5 are not cut.

## Datasets

### 1. Primary public dataset: PTC, SemEval-2020 Task 11
- English news articles with **span-level** labels, 14 techniques — lets us measure both technique and evidence words.
- **Requires registration** (user-only): propaganda.qcri.org/ptc/registration.php, then "PTC Corpus – Version 2" from the team page. HuggingFace has only the loader script `sem_eval_2020_task_11`. Roughly 371 train / 75 dev / 90 test articles; licence "unknown" — check terms.
- Adapting to headlines: split articles into sentences; a sentence with one dominant technique from our six → labelled example with spans; a sentence with no spans → "no technique" (source for FPR); short sentences (≤25 words) are closest to headlines.

### 2. Taxonomy mapping (PTC 14 → our 6)
| Ours | PTC labels |
|---|---|
| Loaded language and labels | Loaded_Language, Name_Calling,Labeling |
| Appeal to fear | Appeal_to_fear-prejudice |
| Exaggeration or minimization | Exaggeration,Minimisation |
| Doubt | Doubt |
| Vague authority | Appeal_to_Authority |
| Changing the subject | Whataboutism,Straw_Men,Red_Herring |
| — out of taxonomy | Flag-Waving, Causal_Oversimplification, Slogans, Black-and-White_Fallacy, Thought-terminating_Cliches, Repetition, Bandwagon,Reductio_ad_hitlerum |

Sentences with out-of-taxonomy techniques are **excluded from evaluation** (labelling them "no technique" would punish correct detections). State this under "Known limitations".

### 3. Additional public sources
- **SemEval-2023 Task 3, subtask 3** (propaganda.math.unipd.it/semeval2023task3): English news paragraphs, ~19 techniques, registration still open — a second independent test set.
- **SemEval-2021 Task 6**: meme texts, 22 techniques, spans, 950 examples — short, closest to headlines. Availability to be confirmed.

### 4. Own headline gold set (required regardless)
- 150–200 **real** headlines from the stage-6 RSS pipeline — our target domain.
- ~35–40% "no technique", the rest spread over the six techniques, ideally as "one event, several outlets" clusters.
- **Two people label independently** (technique + evidence words) — user-only work; compute Cohen's κ; a third person resolves disagreements; κ is shown as the human ceiling.
- Label before looking at model outputs. A simple labelling form (possibly inside our own UI) saves time.
- Always state "n=…, small sample, wide intervals".

### 5. Honest-evaluation rules
- **Dev** (few-shot, prompt tuning) and **test** (final measurement only) never overlap. Freeze and version the test set in git before any prompt tuning.
- LLM-generated headlines: few-shot and demo only, **never evaluation**.
- /metrics reports per source (PTC sentences, our gold set, SemEval-2023 if available), never one merged number.
- Disputed cases from the game go to dev only, after review.

## Done means
- Public URL on Hostinger opens from a clean device; a round plays to the end and the score matches the formula.
- `eval` runs on the frozen test set without errors; /metrics shows per-technique P/R/F1, FPR on "no technique", confusion matrix, agreement calibration, sample sizes.
- `POST /api/analyze` returns spans that are exact substrings of the input on 5–10 manual examples.
- A dispute walks through all statuses and leaves the test metrics unchanged.
