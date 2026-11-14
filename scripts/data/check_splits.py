"""Check that dev and eval data do not overlap.

Every text is normalized (Unicode NFKC, lowercase, punctuation removed, whitespace
collapsed). The check fails (exit code 1) when:

- a normalized text appears in any dev file and in any eval file, or
- a normalized text appears twice inside one file.

Texts shared between two dev files are allowed (``fewshot.jsonl`` is a subset of
the dev set by design). Only ids are printed, never the texts themselves.

Usage:
    python scripts/data/check_splits.py                       # repo defaults
    python scripts/data/check_splits.py --dev data/dev --eval data/staging/eval_v1_candidate
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DEV = [REPO_ROOT / "data" / "dev"]
DEFAULT_EVAL = [REPO_ROOT / "data" / "eval", REPO_ROOT / "data" / "staging" / "eval_v1_candidate"]

_WS = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    text = unicodedata.normalize("NFKC", text).lower()
    text = "".join(" " if unicodedata.category(ch).startswith("P") else ch for ch in text)
    return _WS.sub(" ", text).strip()


@dataclass
class SplitReport:
    dev_files: int = 0
    eval_files: int = 0
    records_checked: int = 0
    within_file_duplicates: list[tuple[str, list[str]]] = field(default_factory=list)
    cross_overlaps: list[tuple[list[str], list[str]]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        has_both_sides = self.dev_files > 0 and self.eval_files > 0
        return has_both_sides and not self.within_file_duplicates and not self.cross_overlaps


def collect_jsonl(paths: list[Path]) -> list[Path]:
    """Expand directories into their *.jsonl files (recursively); keep explicit files."""
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(path.rglob("*.jsonl")))
        elif path.is_file():
            files.append(path)
    return files


def _read_texts(path: Path) -> list[tuple[str, str]]:
    """Return (id, normalized text) pairs for one JSONL file."""
    rows: list[tuple[str, str]] = []
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            rid = str(record.get("id", f"{path.name}:{line_no}"))
            rows.append((rid, normalize_text(record["text"])))
    return rows


def check_splits(dev_paths: list[Path], eval_paths: list[Path]) -> SplitReport:
    report = SplitReport()
    dev_index: dict[str, list[str]] = defaultdict(list)
    eval_index: dict[str, list[str]] = defaultdict(list)

    dev_files, eval_files = collect_jsonl(dev_paths), collect_jsonl(eval_paths)
    report.dev_files, report.eval_files = len(dev_files), len(eval_files)
    for files, index in ((dev_files, dev_index), (eval_files, eval_index)):
        for path in files:
            seen: dict[str, list[str]] = defaultdict(list)
            for rid, norm in _read_texts(path):
                report.records_checked += 1
                seen[norm].append(rid)
                index[norm].append(f"{path.name}:{rid}")
            for norm, ids in seen.items():
                if len(ids) > 1:
                    report.within_file_duplicates.append((str(path), ids))

    for norm in sorted(set(dev_index) & set(eval_index)):
        report.cross_overlaps.append((dev_index[norm], eval_index[norm]))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dev", nargs="+", type=Path, default=DEFAULT_DEV, help="dev files or directories")
    parser.add_argument("--eval", nargs="+", type=Path, default=DEFAULT_EVAL, help="eval files or directories")
    args = parser.parse_args(argv)

    report = check_splits(args.dev, args.eval)
    print(
        f"checked {report.dev_files} dev files, {report.eval_files} eval files, "
        f"{report.records_checked} records"
    )
    for path, ids in report.within_file_duplicates:
        print(f"DUPLICATE in {path}: {', '.join(ids)}")
    for dev_ids, eval_ids in report.cross_overlaps:
        print(f"OVERLAP dev [{', '.join(dev_ids)}] <-> eval [{', '.join(eval_ids)}]")
    if report.dev_files == 0 or report.eval_files == 0:
        print("FAIL: need at least one dev and one eval JSONL file")
        return 1
    if not report.ok:
        print(
            f"FAIL: {len(report.cross_overlaps)} dev/eval overlaps, "
            f"{len(report.within_file_duplicates)} within-file duplicates"
        )
        return 1
    print("OK: no dev/eval overlap, no within-file duplicates")
    return 0


if __name__ == "__main__":
    sys.exit(main())
