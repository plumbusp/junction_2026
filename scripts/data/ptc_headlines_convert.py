"""Convert PTC (SemEval-2020 Task 11) article titles into an eval source: ptc_headlines.jsonl.

Input (read-only, gitignored), in ``--raw-dir``: the ``datasets`` folder of datasets-v2.tgz from
https://zenodo.org/records/3952415 (CC-BY-4.0). Only the train split has technique labels, so only
train articles are used. We never tune on PTC: no PTC text is in the dev set or the prompt anchors.

Each article file is one sentence or paragraph per line; line 0 is the article title, which is a
real news headline annotated span by span by professional annotators (not by an LLM).

Mapping and labelling rules (same as SemEval-2021, docs/KNOWLEDGE_BASE.md, section 3):
- no propaganda span overlaps the title -> no_technique;
- exactly one of our techniques after mapping -> that technique, evidence from mapped spans only;
- two or more of our techniques -> excluded;
- only out-of-taxonomy spans (Flag-Waving, Slogans, ...) -> excluded, never relabelled as no_technique.

Spans are clipped to the title, trimmed, deduplicated and ordered by position (always exact
substrings of the text). Duplicate titles are kept once.

Usage:
    python scripts/data/ptc_headlines_convert.py --raw-dir PATH/datasets --out-dir data/eval/eval_v3
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

# PTC label -> our technique id. Anything else is out of taxonomy.
MAPPING = {
    "Loaded_Language": "loaded_language",
    "Name_Calling,Labeling": "loaded_language",
    "Appeal_to_fear-prejudice": "appeal_to_fear",
    "Exaggeration,Minimisation": "exaggeration_minimization",
    "Doubt": "doubt",
    "Appeal_to_Authority": "vague_authority",
    "Whataboutism,Straw_Men,Red_Herring": "changing_subject",
}
OUT_NAME = "ptc_headlines.jsonl"


def read_labels(path: Path) -> list[tuple[str, int, int]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            _, technique, start, end = line.split("\t")
            rows.append((technique, int(start), int(end)))
    return rows


def convert(raw_dir: Path) -> tuple[list[dict], Counter]:
    excluded: Counter = Counter()
    rows, seen = [], set()
    for article in sorted((raw_dir / "train-articles").glob("article*.txt")):
        art_id = article.stem.removeprefix("article")
        text = article.read_text(encoding="utf-8")
        title = text.split("\n", 1)[0]
        end = len(title)
        stripped = title.strip()
        if not stripped:
            excluded["empty_title"] += 1
            continue
        if stripped.lower() in seen:
            excluded["duplicate_title"] += 1
            continue
        labels_path = raw_dir / "train-labels-task2-technique-classification" / f"article{art_id}.task2-TC.labels"
        hits = [(t, s, min(e, end)) for t, s, e in read_labels(labels_path) if s < end]
        ours = [(MAPPING[t], s, e) for t, s, e in hits if t in MAPPING]
        if hits and not ours:
            excluded["out_of_taxonomy_only"] += 1
            continue
        if len({t for t, _, _ in ours}) > 1:
            excluded["multi_technique"] += 1
            continue
        spans = sorted({(s, title[s:e].strip()) for _, s, e in ours if title[s:e].strip()})
        seen.add(stripped.lower())
        rows.append({
            "id": f"ptc-{art_id}",
            "source": "ptc_headlines",
            "text": stripped,
            "technique": ours[0][0] if ours else "no_technique",
            "evidence_spans": list(dict.fromkeys(span for _, span in spans)),
            "upstream_article": art_id,
        })
    return rows, excluded


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    rows, excluded = convert(args.raw_dir)
    out = args.out_dir / OUT_NAME
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    print(json.dumps({
        "file": str(out),
        "sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
        "n": len(rows),
        "counts": dict(Counter(r["technique"] for r in rows)),
        "excluded_by_reason": dict(excluded),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
