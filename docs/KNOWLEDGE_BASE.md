# Knowledge base: Spot the Spin

Written for: the team and new project members.

## 1. The hackathon challenge (Junction 2026, Hostinger challenge)

Build an AI-powered tool that detects propaganda, bots or bad actors online. It must run **in production on Hostinger, with a public URL** that the judges open themselves.

The judges look first at:
- honesty and measurability of detection: precision, false-positive rate, how false positives are handled;
- live deployment on Hostinger's infrastructure;
- transparency of the verdict.

A polished front end is secondary.

Directions from the brief: spot the swarm, **name the technique** (ours), clean the traffic, trust the source. Directions can be combined.

Resources from the brief:
- Hostinger connector: docs.hostinger.com/hostinger-connector
- API: developers.hostinger.com
- Open-source MCP server: github.com/hostinger/api-mcp-server
- Mentors at the Hostinger booth and a help channel for the weekend (link given at kick-off).

## 2. Our product

**Spot the Spin** is a daily game that teaches people to see manipulation in the news, plus a tool in which the same classifier analyses texts.

- Round: three headlines about one event. The player picks a technique or "no technique", marks evidence words, sees the reference answer, score and system confidence, and can dispute the answer.
- Analyze mode: paste text or a URL, get highlighted phrases, the technique and an explanation.
- /metrics page: P/R/F1 per technique, FPR on "no technique", confusion matrix, model-agreement calibration.

More: `docs/PLAN.md`; core contract: `docs/ARCHITECTURE.md`.

## 3. Manipulation techniques

Six techniques from the team's presentation, plus "no technique". This is our own taxonomy, not SemEval's.

| Technique (id) | What it is | PTC labels (SemEval-2020 T11) |
|---|---|---|
| Loaded language and labels (`loaded_language`) | emotionally charged words and labels | Loaded_Language, Name_Calling, Labeling |
| Appeal to fear (`appeal_to_fear`) | pressure through fear | Appeal_to_fear-prejudice |
| Exaggeration or minimization (`exaggeration_minimization`) | overstating or understating | Exaggeration, Minimisation |
| Doubt (`doubt`) | hints of doubt without evidence | Doubt |
| Vague authority (`vague_authority`) | reference to unnamed "experts" | Appeal_to_Authority |
| Changing the subject (`changing_subject`) | deflection from the question | Whataboutism, Straw_Men, Red_Herring |
| No technique (`no_technique`) | ordinary information, no manipulation | — |

PTC techniques outside our six (Flag-Waving, Slogans, Repetition and others) are **excluded** from evaluation, not labelled as "no technique".

## 4. Review comments (the team's slide)

1. There is no /metrics page. It is about 40% of the score and the top priority.
2. "Three runs of one model" is a weak signal. Use three different models through AI Router.
3. The game is not yet a tool. An Analyze mode is needed.
4. The headlines in the presentation are invented. The game needs real news of the day.
5. Player disputes are unused. A dispute loop is needed, and disputed cases go only into dev.
6. Slides 2 and 7 have text cut off on the right. The 40 × F1 formula (the 27 on slide 5) should be put on a slide.

## 5. Datasets

| Source | What | Status |
|---|---|---|
| PTC, SemEval-2020 Task 11 | news articles, span-level labels, 14 techniques | registration required: propaganda.qcri.org/ptc/registration.php. Status not confirmed |
| SemEval-2023 Task 3 (subtask 3) | English news paragraphs, ~19 techniques | registration open. Status not confirmed |
| SemEval-2021 Task 6 | meme texts, 22 techniques | availability not verified |
| Own gold set of real headlines | 150–200 headlines from RSS, labelled by two people | **not created**. The repository has no `data/` yet |

Important: the repository has no `data/eval/` yet. There is no frozen test set; it has to be created and frozen before prompt tuning.

## 6. Key decisions

- An ensemble of three different models through Hostinger AI Router. Confidence = share of agreeing models, not a probability.
- One classifier for the game, Analyze and /metrics.
- The test set is frozen before prompt tuning. Dev and test do not overlap.
- Accuracy and measurability come first. If time runs short, cut disputes first, then RSS.

## 7. Open questions

- Confirm the PTC registration and the status of SemEval-2023.
- Find a second annotator for the gold set.
- Confirm the AI Router base URL in the panel, and the list of available models (done: the list was retrieved and three models were verified).
- Decide whether Hostinger AI Builder is used for the frontend or the frontend is written by hand.
