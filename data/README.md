# Data

Datasets for Spot the Spin. Honesty rules: `docs/RULES.md`, section 3. Record format: `docs/ARCHITECTURE.md`, "Data formats".

## Layout

| Path | What | In git |
|---|---|---|
| `raw/semeval2021_task6/` | upstream SemEval-2021 Task 6 files, read-only | no (gitignored) |
| `dev/semeval21_dev.jsonl` | upstream train + dev, `no_technique` and single-technique examples | yes |
| `dev/semeval21_dev_multi.jsonl` | upstream train + dev with two or more of our techniques; field `techniques` (list) instead of `technique`, plus `evidence_by_technique` | yes |
| `dev/headlines_dev.jsonl` | 81 real news headline titles (11 same-story rows removed after an overlap check against eval) (source `headlines_llm`) labelled by ONE LLM annotator, not humans; dev only (retrieval few-shot and tuning), never mixed into `eval/`. Manifest: `dev/headlines_dev_manifest.json` | yes |
| `dev/fewshot.jsonl` | the fixed prompt anchors (14 examples: mostly headlines from `headlines_dev.jsonl`, one meme for `changing_subject`) | yes |
| `dev/semeval21_dev_manifest.json` | dev counts, exclusions, seed, input and output SHA-256 | yes |
| `staging/eval_v1_candidate/` | **unfrozen** eval candidate from the upstream test split, with `manifest.json` | no (gitignored) |
| `eval/eval_v1/` | frozen: SemEval-2021 Task 6 test split, 135 texts | yes |
| `eval/eval_v2/` | frozen: `eval_v1` texts unchanged plus 70 real headlines (`headlines_llm`, labelled by one LLM annotator, not humans; a stopgap until two human annotators label a gold set) | yes |
| `eval/eval_v3/` | frozen: `eval_v2` files unchanged plus `ptc_headlines.jsonl`, 332 news article titles from PTC (SemEval-2020 Task 11) with **human** labels; the current test set | yes |

## Sources and licence

- **SemEval-2021 Task 6, subtask 2** (source tag `semeval21`): meme texts with span-level persuasion technique labels. Upstream: https://github.com/di-dimitrov/SEMEVAL-2021-task6-corpus. Licence: "free for general research use". Cite Dimitrov et al., SemEval-2021 Task 6 (https://arxiv.org/abs/2105.09284).
- Upstream ids are kept: `semeval21-<split>-<upstream id>`, where `<split>` is `train`, `dev` or `test`.
- Meme texts are a domain shift from news headlines and some are offensive. Review `dev/fewshot.jsonl` by hand before it goes into a prompt.

## Mapping and labelling rules

Authoritative: `docs/KNOWLEDGE_BASE.md`, section 3, "SemEval-2021 Task 6 mapping". In short:

- no labels: `no_technique`;
- exactly one of our techniques after mapping (other labels may be out of taxonomy): that technique, with evidence from the mapped labels only;
- two or more of our techniques: excluded from eval; kept in `dev/semeval21_dev_multi.jsonl`;
- only out-of-taxonomy labels: excluded everywhere, never relabelled as `no_technique`.

Evidence spans are exact substrings of `text`. A label span is `text[start:end]` when that equals the upstream `text_fragment`; otherwise the `text_fragment` is used if it occurs in `text`; otherwise the span is dropped and counted in the manifest. Surrounding whitespace is trimmed; spans are deduplicated and ordered by position.

Leakage guard: an upstream train/dev example whose normalized text also occurs in the upstream test file is removed from dev (never from eval). Repeated texts within one output file are removed (first occurrence wins). Normalization: NFKC, lowercase, punctuation removed, whitespace collapsed.

## Regenerate

From the repository root (Python runs in WSL with uv):

```
uv run python scripts/data/semeval21_convert.py
uv run python scripts/data/check_splits.py
```

The conversion is deterministic (fixed seed `20211`, recorded in both manifests); only the `date` field in the manifests changes between runs. `check_splits.py` exits non-zero if any normalized text appears in both dev and eval (`data/eval/` and `data/staging/eval_v1_candidate/`), or twice in one file.

## Freezing the eval set

`data/staging/eval_v1_candidate/` is a candidate only. It becomes `data/eval/eval_v1/` **only after explicit user approval**. After freezing, `data/eval/` is never changed and never used for few-shot examples, prompt tuning or thresholds; a change means a new version `eval_v(N+1)`, again with approval. The converter refuses to write under `data/eval/`.
