"""Convert SemEval-2021 Task 6 (subtask 2) into the Spot the Spin JSONL format.

Inputs (read-only, gitignored), in ``--raw-dir``:
    training_set_task2.txt, dev_set_task2.txt, test_set_task2.txt
Each is a JSON array of {"id", "text", "labels": [{"start", "end", "technique", "text_fragment"}]}.

Outputs:
    <dev-dir>/semeval21_dev.jsonl           upstream train+dev, no_technique + single technique
    <dev-dir>/semeval21_dev_multi.jsonl     upstream train+dev, two or more of our techniques
    <dev-dir>/fewshot.jsonl                 up to 2 per technique, picked from semeval21_dev.jsonl
    <dev-dir>/semeval21_dev_manifest.json   dev counts and provenance
    <staging-dir>/semeval21_test.jsonl      upstream test, no_technique + single technique (UNFROZEN candidate)
    <staging-dir>/manifest.json             counts, exclusions, seed, date, licence, sha256

Mapping and labelling rules: docs/KNOWLEDGE_BASE.md, section 3, "SemEval-2021 Task 6 mapping".
- no labels at all -> no_technique;
- exactly one of our techniques after mapping -> that technique, evidence from mapped labels only;
- two or more of our techniques -> excluded from eval, kept in the dev "multi" file;
- only out-of-taxonomy labels -> excluded everywhere (never relabelled as no_technique).

Spans: a label span is ``text[start:end]`` when that equals ``text_fragment``; otherwise the
``text_fragment`` is used if it is an exact substring of ``text``; otherwise the span is dropped and
counted. Surrounding whitespace is trimmed (the result is still an exact substring). Spans are
deduplicated and ordered by their position in the text.

Leakage guard: any upstream train/dev example whose normalized text also occurs in the upstream test
file is removed from dev (never from eval). Duplicate texts inside one output file are removed too.

Usage:
    python scripts/data/semeval21_convert.py [--raw-dir ...] [--dev-dir ...] [--staging-dir ...]
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import random
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_splits import normalize_text

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE = "semeval21"
SEED = 20211
UPSTREAM_URL = "https://github.com/di-dimitrov/SEMEVAL-2021-task6-corpus"
LICENCE = "free for general research use"
RAW_FILES = {
    "train": "training_set_task2.txt",
    "dev": "dev_set_task2.txt",
    "test": "test_set_task2.txt",
}
FEWSHOT_PER_TECHNIQUE = 2
FEWSHOT_MIN_WORDS = 3
FEWSHOT_MAX_WORDS = 40

# Keep in sync with backend/app/core/types.py (scripts do not import the backend package).
TECHNIQUES = [
    "loaded_language",
    "appeal_to_fear",
    "exaggeration_minimization",
    "doubt",
    "vague_authority",
    "changing_subject",
    "no_technique",
]

# Upstream label -> our technique; None means out of taxonomy (docs/KNOWLEDGE_BASE.md, section 3).
LABEL_MAP: dict[str, str | None] = {
    "Loaded Language": "loaded_language",
    "Name calling/Labeling": "loaded_language",
    "Appeal to fear/prejudice": "appeal_to_fear",
    "Exaggeration/Minimisation": "exaggeration_minimization",
    "Doubt": "doubt",
    "Appeal to authority": "vague_authority",
    "Whataboutism": "changing_subject",
    "Misrepresentation of Someone's Position (Straw Man)": "changing_subject",
    "Presenting Irrelevant Data (Red Herring)": "changing_subject",
    "Smears": None,
    "Slogans": None,
    "Glittering generalities (Virtue)": None,
    "Flag-waving": None,
    "Causal Oversimplification": None,
    "Repetition": None,
    "Thought-terminating cliché": None,
    "Black-and-white Fallacy/Dictatorship": None,
    "Reductio ad hitlerum": None,
    # The knowledge base writes it "Obfuscation/Intentional vagueness/Confusion"; upstream uses commas.
    "Obfuscation, Intentional vagueness, Confusion": None,
    "Bandwagon": None,
}

# Exclusion / routing reasons.
MULTI = "multi_technique"
OUT_OF_TAXONOMY = "out_of_taxonomy_only"
EMPTY_TEXT = "empty_text"
DUPLICATE = "duplicate_text_in_split"
TEST_OVERLAP = "overlaps_upstream_test"


@dataclass
class Example:
    upstream_id: str
    split: str
    text: str
    techniques: list[str]  # our techniques present, in TECHNIQUES order
    spans: dict[str, list[str]]  # technique -> evidence spans
    out_of_taxonomy: bool  # at least one out-of-taxonomy label
    has_labels: bool

    @property
    def id(self) -> str:
        return f"{SOURCE}-{self.split}-{self.upstream_id}"

    @property
    def category(self) -> str:
        if not self.has_labels:
            return "no_technique"
        if not self.techniques:
            return OUT_OF_TAXONOMY
        if len(self.techniques) > 1:
            return MULTI
        return "single"


@dataclass
class SpanStats:
    total: int = 0
    repaired: int = 0  # offsets wrong, text_fragment found in text
    dropped: int = 0  # no exact match anywhere
    dropped_ids: list[str] = field(default_factory=list)
    dropped_unused: int = 0  # out-of-taxonomy label spans that could not be grounded either


def resolve_span(text: str, label: dict) -> tuple[int, str, bool] | None:
    """Return (position, exact substring, repaired) for a label, or None if it cannot be grounded.

    `repaired` is True when the offsets did not match and the text_fragment was located instead.
    """
    start, end, fragment = label.get("start"), label.get("end"), label.get("text_fragment") or ""
    offsets_ok = isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(text)
    if offsets_ok and text[start:end] == fragment:
        pos, span, repaired = start, fragment, False
    elif fragment and fragment in text:
        pos = _nearest(text, fragment, start if isinstance(start, int) else 0)
        span, repaired = fragment, True
    else:
        return None
    stripped = span.strip()
    if not stripped:
        return None
    return pos + (len(span) - len(span.lstrip())), stripped, repaired


def _nearest(text: str, fragment: str, start: int) -> int:
    positions, i = [], text.find(fragment)
    while i != -1:
        positions.append(i)
        i = text.find(fragment, i + 1)
    return min(positions, key=lambda p: abs(p - start))


def parse_record(record: dict, split: str, stats: SpanStats) -> Example:
    text = record["text"]
    per_technique: dict[str, list[tuple[int, str]]] = {}
    out_of_taxonomy = False
    for label in record.get("labels", []):
        name = label["technique"]
        if name not in LABEL_MAP:
            raise ValueError(f"unknown SemEval-2021 label {name!r} in {split}/{record['id']}")
        technique = LABEL_MAP[name]
        if technique is None:
            out_of_taxonomy = True
            if resolve_span(text, label) is None:
                stats.dropped_unused += 1  # out-of-taxonomy span; never used as evidence
            continue
        per_technique.setdefault(technique, [])
        stats.total += 1
        resolved = resolve_span(text, label)
        if resolved is None:
            stats.dropped += 1
            stats.dropped_ids.append(f"{SOURCE}-{split}-{record['id']}")
            continue
        pos, span, repaired = resolved
        stats.repaired += repaired
        per_technique[technique].append((pos, span))

    techniques = [t for t in TECHNIQUES if t in per_technique]
    spans = {}
    for technique in techniques:
        ordered = [s for _, s in sorted(per_technique[technique], key=lambda ps: ps[0])]
        spans[technique] = list(dict.fromkeys(ordered))
    return Example(
        upstream_id=str(record["id"]),
        split=split,
        text=text,
        techniques=techniques,
        spans=spans,
        out_of_taxonomy=out_of_taxonomy,
        has_labels=bool(record.get("labels")),
    )


def to_single_row(ex: Example) -> dict:
    if ex.category == "no_technique":
        technique, evidence = "no_technique", []
    else:
        technique = ex.techniques[0]
        evidence = ex.spans[technique]
    return {"id": ex.id, "source": SOURCE, "text": ex.text, "technique": technique, "evidence_spans": evidence}


def to_multi_row(ex: Example) -> dict:
    all_spans = list(dict.fromkeys(s for t in ex.techniques for s in ex.spans[t]))
    return {
        "id": ex.id,
        "source": SOURCE,
        "text": ex.text,
        "techniques": ex.techniques,
        "evidence_spans": all_spans,
        "evidence_by_technique": ex.spans,
    }


def load_split(raw_dir: Path, split: str, stats: SpanStats) -> list[Example]:
    with (raw_dir / RAW_FILES[split]).open(encoding="utf-8") as fh:
        records = json.load(fh)
    return [parse_record(r, split, stats) for r in records]


def dedupe(examples: list[Example], excluded: Counter, blocked: set[str] | None = None) -> list[Example]:
    """Drop empty texts, texts in `blocked`, and repeated texts (first occurrence wins)."""
    seen: set[str] = set()
    kept = []
    for ex in examples:
        norm = normalize_text(ex.text)
        if not norm:
            excluded[EMPTY_TEXT] += 1
        elif blocked is not None and norm in blocked:
            excluded[TEST_OVERLAP] += 1
        elif norm in seen:
            excluded[DUPLICATE] += 1
        else:
            seen.add(norm)
            kept.append(ex)
    return kept


def shuffled(rows: list[dict], seed: int) -> list[dict]:
    rows = sorted(rows, key=lambda r: r["id"])
    random.Random(seed).shuffle(rows)
    return rows


def pick_fewshot(rows: list[dict], seed: int) -> tuple[list[dict], list[str]]:
    """Up to FEWSHOT_PER_TECHNIQUE short examples per technique; returns (rows, missing techniques)."""
    rng = random.Random(seed)
    picked, missing = [], []
    for technique in TECHNIQUES:
        candidates = sorted(
            (
                r
                for r in rows
                if r["technique"] == technique
                and FEWSHOT_MIN_WORDS <= len(r["text"].split()) <= FEWSHOT_MAX_WORDS
                and (technique == "no_technique" or r["evidence_spans"])
            ),
            key=lambda r: r["id"],
        )
        if not candidates:
            missing.append(technique)
            continue
        picked.extend(rng.sample(candidates, min(FEWSHOT_PER_TECHNIQUE, len(candidates))))
    return picked, missing


def write_jsonl(path: Path, rows: list[dict]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode("utf-8")
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def technique_counts(rows: list[dict]) -> dict[str, int]:
    counts = Counter(r["technique"] for r in rows)
    return {t: counts.get(t, 0) for t in TECHNIQUES}


def convert(raw_dir: Path, dev_dir: Path, staging_dir: Path, seed: int = SEED, date: str | None = None) -> dict:
    date = date or dt.date.today().isoformat()
    inputs = {split: {"file": name, "sha256": sha256_file(raw_dir / name)} for split, name in RAW_FILES.items()}

    # --- eval candidate: upstream test ---
    test_stats = SpanStats()
    test = load_split(raw_dir, "test", test_stats)
    test_blocked = {n for n in (normalize_text(ex.text) for ex in test) if n}
    test_excluded: Counter = Counter()
    test_kept = []
    for ex in test:
        if ex.category in (MULTI, OUT_OF_TAXONOMY):
            test_excluded[ex.category] += 1
        else:
            test_kept.append(ex)
    test_kept = dedupe(test_kept, test_excluded)
    test_rows = shuffled([to_single_row(ex) for ex in test_kept], seed)

    # --- dev: upstream train + dev ---
    dev_stats = SpanStats()
    dev_all = load_split(raw_dir, "train", dev_stats) + load_split(raw_dir, "dev", dev_stats)
    dev_excluded: Counter = Counter()
    dev_routable = []
    for ex in dev_all:
        if ex.category == OUT_OF_TAXONOMY:
            dev_excluded[OUT_OF_TAXONOMY] += 1
        else:
            dev_routable.append(ex)
    dev_routable = dedupe(dev_routable, dev_excluded, blocked=test_blocked)
    dev_rows = shuffled([to_single_row(ex) for ex in dev_routable if ex.category != MULTI], seed)
    multi_rows = shuffled([to_multi_row(ex) for ex in dev_routable if ex.category == MULTI], seed)
    fewshot_rows, fewshot_missing = pick_fewshot(dev_rows, seed)

    # --- write ---
    test_name = "semeval21_test.jsonl"
    test_sha = write_jsonl(staging_dir / test_name, test_rows)
    dev_files = {
        "semeval21_dev.jsonl": write_jsonl(dev_dir / "semeval21_dev.jsonl", dev_rows),
        "semeval21_dev_multi.jsonl": write_jsonl(dev_dir / "semeval21_dev_multi.jsonl", multi_rows),
        "fewshot.jsonl": write_jsonl(dev_dir / "fewshot.jsonl", fewshot_rows),
    }

    common = {
        "source": SOURCE,
        "upstream": "SemEval-2021 Task 6, subtask 2",
        "upstream_url": UPSTREAM_URL,
        "licence": LICENCE,
        "mapping": "docs/KNOWLEDGE_BASE.md, section 3, 'SemEval-2021 Task 6 mapping'",
        "generator": "scripts/data/semeval21_convert.py",
        "seed": seed,
        "date": date,
    }
    eval_manifest = {
        **common,
        "status": "UNFROZEN candidate; not data/eval. Freezing requires explicit user approval.",
        "upstream_split": "test",
        "inputs": {"test": inputs["test"]},
        "n_upstream": len(test),
        "n_written": len(test_rows),
        "counts_per_technique": {test_name: technique_counts(test_rows)},
        "excluded_by_reason": dict(sorted(test_excluded.items())),
        "spans": {
            "mapped_label_spans": test_stats.total,
            "repaired_from_text_fragment": test_stats.repaired,
            "dropped_not_in_text": test_stats.dropped,
            "dropped_in_ids": test_stats.dropped_ids,
            "out_of_taxonomy_spans_not_in_text": test_stats.dropped_unused,
        },
        "files": {test_name: {"sha256": test_sha, "n": len(test_rows)}},
    }
    write_json(staging_dir / "manifest.json", eval_manifest)

    dev_manifest = {
        **common,
        "upstream_split": "train+dev",
        "inputs": {"train": inputs["train"], "dev": inputs["dev"]},
        "n_upstream": len(dev_all),
        "counts_per_technique": {
            "semeval21_dev.jsonl": technique_counts(dev_rows),
            "fewshot.jsonl": technique_counts(fewshot_rows),
        },
        "multi_technique_rows": len(multi_rows),
        "excluded_by_reason": dict(sorted(dev_excluded.items())),
        "fewshot_missing_techniques": fewshot_missing,
        "spans": {
            "mapped_label_spans": dev_stats.total,
            "repaired_from_text_fragment": dev_stats.repaired,
            "dropped_not_in_text": dev_stats.dropped,
            "dropped_in_ids": dev_stats.dropped_ids,
            "out_of_taxonomy_spans_not_in_text": dev_stats.dropped_unused,
        },
        "files": {
            name: {"sha256": sha, "n": n}
            for (name, sha), n in zip(dev_files.items(), (len(dev_rows), len(multi_rows), len(fewshot_rows)))
        },
    }
    write_json(dev_dir / "semeval21_dev_manifest.json", dev_manifest)
    return {"eval": eval_manifest, "dev": dev_manifest}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert SemEval-2021 Task 6 subtask 2 to Spot the Spin JSONL.")
    parser.add_argument("--raw-dir", type=Path, default=REPO_ROOT / "data" / "raw" / "semeval2021_task6")
    parser.add_argument("--dev-dir", type=Path, default=REPO_ROOT / "data" / "dev")
    parser.add_argument(
        "--staging-dir", type=Path, default=REPO_ROOT / "data" / "staging" / "eval_v1_candidate"
    )
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--date", default=None, help="date recorded in manifests (default: today)")
    args = parser.parse_args(argv)

    if args.staging_dir.resolve().is_relative_to((REPO_ROOT / "data" / "eval").resolve()):
        parser.error("refusing to write under data/eval/: freezing requires explicit user approval")

    result = convert(args.raw_dir, args.dev_dir, args.staging_dir, seed=args.seed, date=args.date)
    for key in ("dev", "eval"):
        m = result[key]
        print(f"[{key}] counts: {json.dumps(m['counts_per_technique'])}")
        print(f"[{key}] excluded: {json.dumps(m['excluded_by_reason'])}")
        print(f"[{key}] spans: total={m['spans']['mapped_label_spans']} "
              f"repaired={m['spans']['repaired_from_text_fragment']} dropped={m['spans']['dropped_not_in_text']} "
              f"oot_ungrounded={m['spans']['out_of_taxonomy_spans_not_in_text']}")
    print(f"[dev] multi-technique rows: {result['dev']['multi_technique_rows']}")
    print(f"[dev] fewshot missing techniques: {result['dev']['fewshot_missing_techniques']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
