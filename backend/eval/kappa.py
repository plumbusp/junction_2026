"""Cohen's kappa between two annotators who labelled the same examples.

Usage: ``uv run python backend/eval/kappa.py labels_a.jsonl labels_b.jsonl``
Each file has one JSON object per line with ``id`` and ``technique``.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from collections.abc import Mapping
from pathlib import Path


def cohen_kappa(a: Mapping[str, str], b: Mapping[str, str]) -> float:
    """Kappa for two {id: label} mappings. Refuses mismatched or empty ID sets."""
    if set(a) != set(b):
        only_a = sorted(set(a) - set(b))[:5]
        only_b = sorted(set(b) - set(a))[:5]
        raise ValueError(
            f"ID sets differ: only in A {only_a}, only in B {only_b} (first 5 shown)"
        )
    n = len(a)
    if n == 0:
        raise ValueError("no examples to compare")
    observed = sum(a[i] == b[i] for i in a) / n
    count_a, count_b = Counter(a.values()), Counter(b.values())
    expected = sum(count_a[label] * count_b[label] for label in count_a) / (n * n)
    if expected == 1:
        raise ValueError("kappa is undefined: both annotators used one and the same label")
    return (observed - expected) / (1 - expected)


def load_labels(path: Path) -> dict[str, str]:
    """Read ``{id: technique}`` from a JSONL file; duplicate IDs are an error."""
    labels: dict[str, str] = {}
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if row["id"] in labels:
            raise ValueError(f"{path}:{line_no}: duplicate id {row['id']!r}")
        labels[row["id"]] = row["technique"]
    return labels


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    a, b = (load_labels(Path(p)) for p in args)
    try:
        kappa = cohen_kappa(a, b)
    except ValueError as exc:
        print(f"refusing to compute kappa: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"cohen_kappa": round(kappa, 4), "n": len(a)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
