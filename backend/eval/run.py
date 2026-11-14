"""Run the core classifier over a frozen eval set and write the results JSON for /metrics.

Usage (from the repository root):
    uv run python backend/eval/run.py --eval-version eval_v1 [--limit N] [--workers K]

The mode follows CORE_MODE. Live results go to ``backend/eval/results/`` and are copied to
``results/latest.json`` (served at GET /api/metrics). Stub results go only to
``backend/eval/results/stub/`` and never to latest.json. A run with ``--limit`` is partial
and is never copied to latest.json.

Order of work: freeze check (sha256 of every file in the manifest; any mismatch prints it,
exits non-zero and writes nothing), load, classify in a thread pool (``classify`` caches
live verdicts, so a re-run after a crash does not pay again for finished items), compute
metrics per source (sources are never merged), write the results and the per-example
predictions. Items whose classification fails are recorded as errors and counted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import uuid
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import get_settings, require_live_key
from app.core.classifier import classify
from app.core.prompt import PROMPT_VERSION
from app.core.stub import is_stub_verdict
from app.core.types import TECHNIQUES, Verdict
from app.game.scoring import token_f1

from eval.metrics import (
    CI_METHOD,
    EvalItem,
    ItemResult,
    model_ids,
    source_metrics,
)

REPO_ROOT = BACKEND_DIR.parent
DATA_EVAL_DIR = REPO_ROOT / "data" / "eval"
RESULTS_DIR = BACKEND_DIR / "eval" / "results"
LATEST_NAME = "latest.json"
STUB_SUBDIR = "stub"
SCHEMA_VERSION = 1
DEFAULT_WORKERS = 4
NO_TECHNIQUE = "no_technique"
SMALL_N = 10

SOURCE_LABELS = {
    "semeval21": "SemEval-2021 Task 6 (meme texts)",
    "semeval23": "SemEval-2023 Task 3 paragraphs",
    "ptc": "PTC sentences (SemEval-2020 Task 11)",
    "gold": "Own headline gold set",
    "headlines_llm": "Real news headlines, labelled by one LLM annotator (not human)",
    "ptc_headlines": "Real news headlines, human-annotated (PTC, SemEval-2020 Task 11)",
}

SOURCE_NOTES = {
    "semeval21": [
        (
            "SemEval-2021 Task 6 texts are meme captions, not news headlines: a domain "
            "shift from the headlines the product is built for."
        ),
        (
            "SemEval-2021 Task 6: no_technique means the upstream annotators marked no "
            "propaganda span in the text; it was not separately judged as neutral."
        ),
    ],
}

SOURCE_NOTES["headlines_llm"] = [
    (
        "Real news headlines: the labels come from ONE LLM annotator, not from humans, "
        "and there is no second annotator, so label noise is unknown and no human "
        "ceiling (Cohen's kappa) exists. The annotator and the ensemble's models are all "
        "general-purpose LLMs, so their errors may be correlated and agreement may be "
        "overstated."
    ),
    (
        "Real news headlines: the annotator labelled from the headline text alone, before "
        "seeing any ensemble output, but picked the 70 headlines from a random sample of "
        "140, so the set is not a random sample of all news and borderline cases were "
        "mostly left out. This is a stopgap until two human annotators label a gold set."
    ),
]

SOURCE_NOTES["ptc_headlines"] = [
    (
        "PTC headlines: titles of 2017-2019 news articles annotated span by span by "
        "professional annotators (Da San Martino et al., CC-BY-4.0). Every train-article "
        "title is used, no sampling; PTC was never used for prompt tuning."
    ),
    (
        "PTC headlines: no_technique means the annotators marked no propaganda span in the "
        "title, not a separate neutrality judgement. Titles were annotated inside the whole "
        "article and some clearly loaded titles carry no span, so part of the false "
        "positives here may be label noise."
    ),
]

GENERAL_NOTES = [
    (
        "Public datasets may be part of the models' training data (possible leakage), "
        "which can inflate scores."
    ),
    (
        "Techniques outside our six are not measured: texts with only such techniques are "
        "excluded, not relabelled as no_technique."
    ),
    (
        "Agreement (3/3, 2/3) is the share of models that voted for the final technique, "
        "not a calibrated probability."
    ),
    (
        "F1 and macro-F1 are not simple proportions: macro-F1 and span token-F1 intervals "
        f"are a {CI_METHOD}; all other intervals are Wilson 95%."
    ),
    (
        "Span token-F1 is averaged over every example with a technique reference and "
        "reference spans, whatever the predicted technique; the value restricted to "
        "correctly classified examples is reported as span_token_f1.when_technique_correct."
    ),
    (
        "Human ceiling (Cohen's kappa between two independent annotators) is not measured "
        "yet: there is no second annotation."
    ),
]

DEFINITIONS = {
    "accuracy": "share of examples whose ensemble technique equals the reference",
    "fpr_no_technique": "share of no_technique references for which the ensemble "
    "predicted some technique",
    "macro_f1": "mean F1 over labels that occur as reference or prediction in the source",
    "f1": "2*tp / (support + predicted)",
    "by_agreement": "accuracy grouped by votes for the final technique; disputed verdicts "
    "carry no_technique",
    "flag_policies": "a text is flagged when the ensemble names a technique with at least "
    "min_agreement votes",
    "per_model": "each model over its valid votes; missing votes are counted in "
    "n_missing_votes",
    "n": "examples scored; failed classifications are counted in n_errors and excluded",
}

STUB_NOTE = (
    "Stub run (CORE_MODE=stub): deterministic fake verdicts, no model was called. "
    "These numbers say nothing about detection quality."
)


class EvalError(Exception):
    """The runner refuses to continue; nothing is written."""


class FreezeError(EvalError):
    """The eval set on disk differs from its manifest."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_freeze(version_dir: Path) -> tuple[dict, str]:
    """Verify every file listed in the manifest. Returns (manifest, manifest sha256).

    Raises FreezeError listing every mismatch: missing manifest, missing file, wrong
    sha256, or a JSONL file in the directory that the manifest does not list.
    """
    manifest_path = version_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FreezeError(f"manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest.get("files") or {}
    if not files:
        raise FreezeError(f"{manifest_path} lists no files")
    problems = []
    for name, meta in files.items():
        path = version_dir / name
        if not path.is_file():
            problems.append(f"{name}: missing")
            continue
        actual = sha256_file(path)
        if actual != meta.get("sha256"):
            problems.append(f"{name}: sha256 {actual} != manifest {meta.get('sha256')}")
    for path in sorted(version_dir.glob("*.jsonl")):
        if path.name not in files:
            problems.append(f"{path.name}: not listed in the manifest")
    if problems:
        raise FreezeError("; ".join(problems))
    return manifest, sha256_file(manifest_path)


def load_items(version_dir: Path, manifest: dict, limit: int | None = None) -> list[EvalItem]:
    """Read and validate the examples of every manifest file, in manifest order."""
    items: list[EvalItem] = []
    seen: set[str] = set()
    for name in manifest["files"]:
        lines = (version_dir / name).read_text(encoding="utf-8").splitlines()
        for line_no, line in enumerate(lines, 1):
            if not line.strip():
                continue
            where = f"{name}:{line_no}"
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EvalError(f"{where}: invalid JSON ({exc.msg})") from exc
            if not isinstance(row, dict):
                raise EvalError(f"{where}: expected a JSON object")
            for key in ("id", "source", "text", "technique"):
                if not isinstance(row.get(key), str) or not row[key]:
                    raise EvalError(f"{where}: missing or empty {key!r}")
            if row["technique"] not in TECHNIQUES:
                raise EvalError(f"{where}: unknown technique {row['technique']!r}")
            spans = row.get("evidence_spans", [])
            if not isinstance(spans, list) or not all(isinstance(s, str) for s in spans):
                raise EvalError(f"{where}: evidence_spans must be a list of strings")
            if row["id"] in seen:
                raise EvalError(f"{where}: duplicate id {row['id']!r}")
            seen.add(row["id"])
            items.append(
                EvalItem(
                    id=row["id"],
                    source=row["source"],
                    text=row["text"],
                    reference=row["technique"],
                    reference_spans=tuple(spans),
                )
            )
    return items[:limit] if limit is not None else items


def _classify_one(item: EvalItem, classify_fn: Callable[[str], Verdict]) -> ItemResult:
    try:
        verdict = classify_fn(item.text)
    except Exception as exc:  # noqa: BLE001 - one failure must not stop the run; it is counted
        return ItemResult(item, None, f"{type(exc).__name__}: {str(exc)[:200]}")
    if not verdict.votes:
        return ItemResult(item, None, "no model returned a usable vote")
    return ItemResult(item, verdict)


def classify_all(
    items: Sequence[EvalItem],
    classify_fn: Callable[[str], Verdict],
    workers: int = DEFAULT_WORKERS,
) -> list[ItemResult]:
    """Classify every item in a thread pool; results keep the input order."""
    results: list[ItemResult | None] = [None] * len(items)
    done = errors = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(_classify_one, item, classify_fn): i for i, item in enumerate(items)
        }
        for future in as_completed(futures):
            result = future.result()
            results[futures[future]] = result
            done += 1
            errors += not result.ok
            if done % 25 == 0 or done == len(items):
                print(f"classified {done}/{len(items)} (errors: {errors})", file=sys.stderr)
    return [r for r in results if r is not None]


def known_limitations(sources: dict[str, dict], manifest: dict) -> list[str]:
    notes: list[str] = []
    for source, metrics in sources.items():
        label = metrics["label"]
        notes.extend(SOURCE_NOTES.get(source, []))
        excluded = metrics.get("excluded") or {}
        if excluded:
            notes.append(
                f"{label}: {excluded.get('multi_technique', 0)} texts with two or more of "
                "our six techniques were excluded (a single reference label would be "
                f"arbitrary); {excluded.get('out_of_taxonomy_only', 0)} texts with only "
                "out-of-taxonomy techniques were excluded."
            )
        counts = metrics["n_per_technique"]
        rare = [f"{t} n={c}" for t, c in counts.items() if 0 < c < SMALL_N]
        if rare:
            notes.append(
                f"{label}: small samples for {', '.join(rare)}; their per-technique "
                "numbers have very wide intervals."
            )
        absent = [t for t, c in counts.items() if c == 0]
        if absent:
            notes.append(
                f"{label}: no reference examples of {', '.join(absent)}; their recall is "
                "not measured."
            )
        if metrics["n_errors"]:
            notes.append(
                f"{label}: {metrics['n_errors']} of {metrics['n_total']} examples failed "
                "to classify and are excluded from the metrics (ids in error_ids)."
            )
    return notes + GENERAL_NOTES


def build_results(
    results: Sequence[ItemResult],
    *,
    mode: str,
    eval_version: str,
    manifest: dict,
    manifest_sha256: str,
    run_at: datetime,
    limit: int | None,
) -> dict:
    """Assemble the results JSON; one entry per source, never merged."""
    models = model_ids([r for r in results if r.ok])
    by_source: dict[str, list[ItemResult]] = {}
    for r in results:
        by_source.setdefault(r.item.source, []).append(r)
    sources = {}
    for source, source_results in by_source.items():
        base = SOURCE_LABELS.get(source, source)
        excluded = manifest.get("excluded_by_reason") if manifest.get("source") == source else None
        sources[source] = source_metrics(
            source_results,
            label=f"{base}, {eval_version}",
            excluded=dict(excluded) if excluded else None,
            models=models,
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "stub_note": STUB_NOTE if mode != "live" else None,
        "run_at": run_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "prompt_version": PROMPT_VERSION,
        "eval_version": eval_version,
        "manifest_sha256": manifest_sha256,
        "core_models": models,
        "techniques": list(TECHNIQUES),
        "limit": limit,
        "n_errors": sum(not r.ok for r in results),
        "sources": sources,
        "known_limitations": known_limitations(sources, manifest),
        "definitions": DEFINITIONS,
    }


def prediction_record(result: ItemResult) -> dict:
    """One line of the per-example predictions file (for error inspection only)."""
    item, verdict = result.item, result.verdict
    record = {
        "id": item.id,
        "source": item.source,
        "reference": {"technique": item.reference, "evidence_spans": list(item.reference_spans)},
        "ensemble": None,
        "votes": [],
        "correct": None,
        "span_token_f1": None,
        "error": result.error,
    }
    if result.ok:
        record["ensemble"] = {
            "technique": verdict.technique,
            "evidence_spans": verdict.evidence_spans,
            "agreement": verdict.agreement,
            "n_models": verdict.n_models,
            "disputed": verdict.disputed,
        }
        record["votes"] = [
            {"model": v.model, "technique": v.technique, "evidence_spans": v.evidence_spans}
            for v in verdict.votes
        ]
        record["correct"] = result.correct
        if item.reference != NO_TECHNIQUE and item.reference_spans:
            record["span_token_f1"] = round(
                token_f1(verdict.evidence_spans, list(item.reference_spans)), 4
            )
    return record


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def run_eval(
    eval_version: str,
    *,
    mode: str,
    classify_fn: Callable[[str], Verdict] = classify,
    data_root: Path = DATA_EVAL_DIR,
    results_root: Path = RESULTS_DIR,
    limit: int | None = None,
    workers: int = DEFAULT_WORKERS,
    now: datetime | None = None,
) -> dict[str, Path]:
    """Freeze check, classify, compute, write. Returns the written paths by role."""
    if mode not in ("live", "stub"):
        raise EvalError(f"unknown mode {mode!r}")
    version_dir = data_root / eval_version
    manifest, manifest_sha = check_freeze(version_dir)  # first; raises before any write
    items = load_items(version_dir, manifest, limit)
    if not items:
        raise EvalError("no examples to evaluate")

    results = classify_all(items, classify_fn, workers)
    ok = [r for r in results if r.ok]
    if not ok:
        raise EvalError(f"all {len(results)} examples failed; first error: {results[0].error}")
    if mode == "live" and any(is_stub_verdict(r.verdict) for r in ok):
        raise EvalError("live run returned stub verdicts; refusing to write real results")

    run_at = (now or datetime.now(UTC)).astimezone(UTC)
    payload = build_results(
        results,
        mode=mode,
        eval_version=eval_version,
        manifest=manifest,
        manifest_sha256=manifest_sha,
        run_at=run_at,
        limit=limit,
    )

    out_dir = results_root / STUB_SUBDIR if mode == "stub" else results_root
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = run_at.strftime("%Y%m%dT%H%M%SZ")
    results_path = out_dir / f"{eval_version}-{stamp}.json"
    predictions_path = out_dir / f"{eval_version}-{stamp}-predictions.jsonl"
    _write_atomic(results_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    _write_atomic(
        predictions_path,
        "".join(json.dumps(prediction_record(r), ensure_ascii=False) + "\n" for r in results),
    )
    paths = {"results": results_path, "predictions": predictions_path}
    if mode == "live" and limit is None:
        latest = results_root / LATEST_NAME
        tmp = latest.with_name(f".{LATEST_NAME}.{uuid.uuid4().hex}.tmp")
        shutil.copyfile(results_path, tmp)
        os.replace(tmp, latest)
        paths["latest"] = latest
    return paths


def _summary(payload: dict) -> str:
    lines = [f"mode={payload['mode']} models={payload['core_models']} errors={payload['n_errors']}"]
    for source, m in payload["sources"].items():
        acc, fpr, mf = m["accuracy"], m["fpr_no_technique"], m["macro_f1"]
        lines.append(
            f"  {source}: n={m['n']} accuracy={acc['value']} {acc['ci']} "
            f"macro_f1={mf['value']} {mf['ci']} fpr_no_technique={fpr['value']} "
            f"({fpr['k']}/{fpr['n']}) {fpr['ci']} span_token_f1={m['span_token_f1']['mean']}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--eval-version", required=True)
    parser.add_argument("--limit", type=int, default=None, help="first N examples (partial run)")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--data-dir", type=Path, default=DATA_EVAL_DIR, help=argparse.SUPPRESS)
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    settings = get_settings()
    mode = settings.core_mode
    try:
        if mode == "live":
            require_live_key(settings)
        paths = run_eval(
            args.eval_version,
            mode=mode,
            data_root=args.data_dir,
            results_root=args.results_dir,
            limit=args.limit,
            workers=args.workers,
        )
    except FreezeError as exc:
        print(f"FREEZE CHECK FAILED for {args.eval_version}: {exc}", file=sys.stderr)
        return 1
    except (EvalError, RuntimeError) as exc:
        print(f"eval refused: {exc}", file=sys.stderr)
        return 1

    payload = json.loads(paths["results"].read_text(encoding="utf-8"))
    print(_summary(payload))
    for role, path in paths.items():
        print(f"wrote {role}: {path}")
    if mode == "live" and "latest" not in paths:
        print("partial run (--limit): latest.json was not updated")
    if payload["n_errors"]:
        print(f"WARNING: {payload['n_errors']} examples failed; re-run to retry them (cache keeps done items)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
