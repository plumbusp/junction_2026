"""Leave-one-out check of the few-shot method on the dev headlines. Dev only.

Usage (from the repository root):
    uv run python backend/eval/dev_check.py --mode retrieval|static [--limit N] [--workers K] [--k K]

Each headline of ``data/dev/headlines_dev.jsonl`` is classified with the examples the
core would pick for it, but never with the headline itself: retrieval skips any example
whose normalized text equals the input, anchors included (``--mode retrieval``), or the
fixed anchors only (``--mode static``, the baseline). Live model calls happen only when
CORE_MODE=live; in stub mode the run is offline and says nothing about quality.

This is a tuning aid, not an evaluation: the labels come from one LLM annotator, the
retrieval pool is the rest of dev, and the numbers must never be reported as eval
results. Files under ``data/eval/`` are refused. The summary JSON goes to
``backend/eval/results/dev/`` (stub runs to ``results/dev/stub/``).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import get_settings, require_live_key
from app.core.classifier import classify_live
from app.core.prompt import PROMPT_VERSION, refuse_eval_path
from app.core.stub import is_stub_verdict, stub_classify
from app.core.types import TECHNIQUES, Verdict

from eval.metrics import EvalItem, ItemResult, classification_metrics, fpr_no_technique
from eval.run import DEFAULT_WORKERS, EvalError, classify_all

REPO_ROOT = BACKEND_DIR.parent
HEADLINES_PATH = REPO_ROOT / "data" / "dev" / "headlines_dev.jsonl"
RESULTS_DIR = BACKEND_DIR / "eval" / "results" / "dev"
STUB_SUBDIR = "stub"
SCHEMA_VERSION = 1
MODES = ("retrieval", "static")

NOTES = [
    "Dev-only leave-one-out check, not an evaluation: do not report it as an eval result.",
    (
        "Reference labels come from one LLM annotator, not from humans; the retrieval "
        "pool is the rest of data/dev (headlines and SemEval-2021 meme texts)."
    ),
    "Per-technique recall uses the n shown; small n gives very wide Wilson intervals.",
]


def load_headlines(path: Path = HEADLINES_PATH, limit: int | None = None) -> list[EvalItem]:
    """Read the dev headlines. Paths inside ``data/eval/`` are refused; bad lines skipped."""
    refuse_eval_path(path)
    if not path.is_file():
        raise EvalError(f"dev headlines not found: {path.name}")
    items: list[EvalItem] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            text, technique, item_id = row.get("text"), row.get("technique"), row.get("id")
            spans = row.get("evidence_spans", [])
            if not (isinstance(text, str) and text.strip() and isinstance(item_id, str)):
                continue
            if technique not in TECHNIQUES or item_id in seen:
                continue
            if not isinstance(spans, list) or not all(isinstance(s, str) for s in spans):
                continue
            seen.add(item_id)
            items.append(
                EvalItem(item_id, str(row.get("source", "headlines")), text, technique, tuple(spans))
            )
    return items[:limit] if limit is not None else items


def make_classify_fn(*, mode: str, retrieval_k: int | None = None) -> Callable[[str], Verdict]:
    """The classifier for this run: live when CORE_MODE=live, else the offline stub."""
    settings = get_settings()
    if settings.core_mode != "live":
        return stub_classify
    require_live_key(settings)
    overrides: dict = {"fewshot_mode": mode}
    if retrieval_k is not None:
        overrides["retrieval_k"] = retrieval_k
    run_settings = dataclasses.replace(settings, **overrides)
    # classify_live selects the examples per text and skips the text's own example.
    return lambda text: classify_live(text, settings=run_settings)


def summarize(results: list[ItemResult]) -> dict:
    """Accuracy, macro-F1 and per-technique recall (with n and Wilson intervals)."""
    ok = [r for r in results if r.ok]
    refs = [r.item.reference for r in ok]
    preds = [r.verdict.technique for r in ok]
    core = classification_metrics(refs, preds)
    return {
        "n": len(ok),
        "n_total": len(results),
        "n_errors": len(results) - len(ok),
        "error_ids": [r.item.id for r in results if not r.ok],
        "accuracy": core["accuracy"],
        "macro_f1": core["macro_f1"],
        "fpr_no_technique": fpr_no_technique(refs, preds),
        "per_technique": {
            t: {
                "n": m["support"],
                "predicted": m["predicted"],
                "recall": m["recall"],
                "precision": m["precision"],
                "f1": m["f1"],
            }
            for t, m in core["per_technique"].items()
        },
        "confusion_matrix": core["confusion_matrix"],
    }


def run_dev_check(
    *,
    mode: str,
    core_mode: str,
    classify_fn: Callable[[str], Verdict],
    headlines_path: Path = HEADLINES_PATH,
    results_root: Path = RESULTS_DIR,
    limit: int | None = None,
    workers: int = DEFAULT_WORKERS,
    retrieval_k: int | None = None,
    now: datetime | None = None,
) -> Path:
    """Classify the dev headlines, compute the metrics, write the summary. Returns its path."""
    if mode not in MODES:
        raise EvalError(f"unknown mode {mode!r}")
    refuse_eval_path(results_root)
    items = load_headlines(headlines_path, limit)
    if not items:
        raise EvalError("no dev headlines to check")

    results = classify_all(items, classify_fn, workers)
    ok = [r for r in results if r.ok]
    if not ok:
        raise EvalError(f"all {len(results)} headlines failed; first error: {results[0].error}")
    if core_mode == "live" and any(is_stub_verdict(r.verdict) for r in ok):
        raise EvalError("live run returned stub verdicts; refusing to write results")

    run_at = (now or datetime.now(UTC)).astimezone(UTC)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "kind": "dev_check_leave_one_out",
        "core_mode": core_mode,
        "stub_note": (
            "Stub run: deterministic fake verdicts, no model was called; the numbers say "
            "nothing about quality."
            if core_mode != "live"
            else None
        ),
        "fewshot_mode": mode,
        "retrieval_k": retrieval_k,
        "prompt_version": PROMPT_VERSION,
        "run_at": run_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_file": headlines_path.name,
        "limit": limit,
        **summarize(results),
        "predictions": [
            {
                "id": r.item.id,
                "reference": r.item.reference,
                "predicted": r.verdict.technique if r.ok else None,
                "agreement": r.verdict.agreement if r.ok else None,
            }
            for r in results
        ],
        "notes": NOTES,
    }
    out_dir = results_root / STUB_SUBDIR if core_mode != "live" else results_root
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = run_at.strftime("%Y%m%dT%H%M%SZ")
    suffix = f"-k{retrieval_k}" if mode == "retrieval" and retrieval_k is not None else ""
    path = out_dir / f"headlines-{mode}{suffix}-{stamp}.json"
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def _print_summary(payload: dict) -> None:
    acc, mf = payload["accuracy"], payload["macro_f1"]
    print(
        f"core_mode={payload['core_mode']} fewshot_mode={payload['fewshot_mode']} "
        f"n={payload['n']} errors={payload['n_errors']}"
    )
    print(f"accuracy {acc['value']} {acc['ci']} ({acc['k']}/{acc['n']})")
    print(f"macro_f1 {mf['value']} {mf['ci']}")
    fpr = payload["fpr_no_technique"]
    print(f"fpr_no_technique {fpr['value']} ({fpr['k']}/{fpr['n']}) {fpr['ci']}")
    print("per-technique recall (value, Wilson 95%, n):")
    for technique, m in payload["per_technique"].items():
        rec = m["recall"]
        print(f"  {technique}: {rec['value']} {rec['ci']} n={m['n']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=MODES, default="retrieval")
    parser.add_argument("--limit", type=int, default=None, help="first N headlines")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--k", type=int, default=None, help="override RETRIEVAL_K (0-12)")
    parser.add_argument("--data-file", type=Path, default=HEADLINES_PATH, help=argparse.SUPPRESS)
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    settings = get_settings()
    k = args.k if args.k is not None else settings.retrieval_k
    try:
        if not 0 <= k <= 12:
            raise EvalError("--k must be between 0 and 12")
        path = run_dev_check(
            mode=args.mode,
            core_mode=settings.core_mode,
            classify_fn=make_classify_fn(mode=args.mode, retrieval_k=k),
            headlines_path=args.data_file,
            results_root=args.results_dir,
            limit=args.limit,
            workers=args.workers,
            retrieval_k=k,
        )
    except (EvalError, RuntimeError, ValueError) as exc:
        print(f"dev check refused: {exc}", file=sys.stderr)
        return 1
    _print_summary(json.loads(path.read_text(encoding="utf-8")))
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
