"""Metric functions for one source of the eval set. Pure: no I/O, no model calls.

Definitions (also written into the results JSON as ``definitions``):
- precision/recall per label are proportions with Wilson 95% intervals;
- F1 = 2*tp / (support + predicted); undefined (None) only when both are 0;
- macro-F1 averages F1 over the labels that occur as reference or prediction in the
  source; its interval is a percentile bootstrap over examples (F1 is not a proportion);
- FPR on no_technique = share of ``no_technique`` references for which the ensemble
  predicted some technique;
- span token-F1 uses ``app.game.scoring.token_f1`` on the predicted vs reference spans.
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.core.types import TECHNIQUES, Verdict
from app.game.scoring import token_f1

from eval.calibration import by_agreement, flag_policies
from eval.wilson import DIGITS, proportion

NO_TECHNIQUE = "no_technique"
ENSEMBLE = "ensemble"
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 0
CI_METHOD = (
    f"percentile bootstrap over examples, {BOOTSTRAP_RESAMPLES} resamples, "
    f"seed {BOOTSTRAP_SEED}"
)


@dataclass(frozen=True)
class EvalItem:
    id: str
    source: str
    text: str
    reference: str  # one of TECHNIQUES
    reference_spans: tuple[str, ...]


@dataclass(frozen=True)
class ItemResult:
    item: EvalItem
    verdict: Verdict | None  # None when classification failed
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.verdict is not None and self.error is None

    @property
    def correct(self) -> bool:
        return self.verdict.technique == self.item.reference


def _round(x: float | None) -> float | None:
    return None if x is None else round(x, DIGITS)


def _f1(tp: int, support: int, predicted: int) -> float | None:
    if support + predicted == 0:
        return None
    return 2 * tp / (support + predicted)


def macro_f1_value(
    refs: Sequence[str], preds: Sequence[str]
) -> tuple[float | None, list[str]]:
    """Macro-F1 over labels that occur in ``refs`` or ``preds``, and those labels."""
    ref_counts, pred_counts = Counter(refs), Counter(preds)
    tp_counts = Counter(r for r, p in zip(refs, preds, strict=True) if r == p)
    labels = [t for t in TECHNIQUES if ref_counts[t] or pred_counts[t]]
    if not labels:
        return None, []
    scores = [_f1(tp_counts[t], ref_counts[t], pred_counts[t]) for t in labels]
    return sum(scores) / len(scores), labels


def bootstrap_ci(
    n: int,
    statistic: Callable[[list[int]], float | None],
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> list[float] | None:
    """Percentile 95% interval of ``statistic(indices)`` over bootstrap resamples."""
    if n < 2:
        return None
    rng = random.Random(seed)
    stats = []
    for _ in range(resamples):
        value = statistic([rng.randrange(n) for _ in range(n)])
        if value is not None:
            stats.append(value)
    if not stats:
        return None
    stats.sort()
    lo = stats[int(0.025 * len(stats))]
    hi = stats[max(0, int(0.975 * len(stats)) - 1)]
    return [round(lo, DIGITS), round(hi, DIGITS)]


def classification_metrics(refs: Sequence[str], preds: Sequence[str]) -> dict:
    """Accuracy, macro-F1, per-technique P/R/F1 and the confusion matrix."""
    refs, preds = list(refs), list(preds)
    n = len(refs)
    per_technique = {}
    for label in TECHNIQUES:
        support = refs.count(label)
        predicted = preds.count(label)
        tp = sum(r == p == label for r, p in zip(refs, preds, strict=True))
        per_technique[label] = {
            "support": support,
            "predicted": predicted,
            "tp": tp,
            "precision": proportion(tp, predicted),
            "recall": proportion(tp, support),
            "f1": _round(_f1(tp, support, predicted)),
        }
    macro, labels = macro_f1_value(refs, preds)
    ci = bootstrap_ci(
        n,
        lambda idx: macro_f1_value([refs[i] for i in idx], [preds[i] for i in idx])[0],
    )
    index = {label: i for i, label in enumerate(TECHNIQUES)}
    matrix = [[0] * len(TECHNIQUES) for _ in TECHNIQUES]
    for r, p in zip(refs, preds, strict=True):
        matrix[index[r]][index[p]] += 1
    return {
        "accuracy": proportion(sum(r == p for r, p in zip(refs, preds, strict=True)), n),
        "macro_f1": {
            "value": _round(macro),
            "ci": ci,
            "ci_method": CI_METHOD if ci else None,
            "labels": len(labels),
            "labels_averaged": labels,
        },
        "per_technique": per_technique,
        "confusion_matrix": {
            "labels": list(TECHNIQUES),
            "rows": "reference",
            "columns": "predicted",
            "matrix": matrix,
        },
    }


def fpr_no_technique(refs: Sequence[str], preds: Sequence[str]) -> dict:
    """Share of no_technique references predicted as some technique (Wilson interval)."""
    clean = [p for r, p in zip(refs, preds, strict=True) if r == NO_TECHNIQUE]
    return proportion(sum(p != NO_TECHNIQUE for p in clean), len(clean))


def _mean_with_ci(values: list[float]) -> dict:
    if not values:
        return {"mean": None, "n": 0, "ci": None}
    return {
        "mean": _round(sum(values) / len(values)),
        "n": len(values),
        "ci": bootstrap_ci(
            len(values), lambda idx: sum(values[i] for i in idx) / len(idx)
        ),
    }


def span_token_f1(results: Sequence[ItemResult]) -> dict:
    """Token-F1 of ensemble evidence spans against reference spans.

    Main figure (``mean``, ``n``): every example whose reference is a technique and has
    reference spans, whatever the ensemble predicted (a wrong or missing technique
    usually gives 0). ``when_technique_correct``: the same, restricted to examples whose
    predicted technique equals the reference.
    """
    scored = [
        r
        for r in results
        if r.item.reference != NO_TECHNIQUE and r.item.reference_spans
    ]
    skipped = sum(
        1 for r in results if r.item.reference != NO_TECHNIQUE and not r.item.reference_spans
    )
    all_f1 = [token_f1(r.verdict.evidence_spans, list(r.item.reference_spans)) for r in scored]
    matched_f1 = [
        f1 for r, f1 in zip(scored, all_f1, strict=True) if r.correct
    ]
    return {
        **_mean_with_ci(all_f1),
        "definition": (
            "mean token-F1 over all examples with a technique reference and reference "
            "spans, regardless of the predicted technique"
        ),
        "n_skipped_no_reference_spans": skipped,
        "when_technique_correct": _mean_with_ci(matched_f1),
        "ci_method": CI_METHOD,
    }


def model_ids(results: Sequence[ItemResult]) -> list[str]:
    """Model IDs that appear in the votes, in first-seen order."""
    seen: dict[str, None] = {}
    for r in results:
        for vote in r.verdict.votes:
            seen.setdefault(vote.model, None)
    return list(seen)


def per_model(results: Sequence[ItemResult], models: Sequence[str]) -> list[dict]:
    """Each model alone (over its valid votes), then the ensemble."""
    out = []
    for model in models:
        refs, preds = [], []
        for r in results:
            vote = next((v for v in r.verdict.votes if v.model == model), None)
            if vote is not None:
                refs.append(r.item.reference)
                preds.append(vote.technique)
        out.append(_model_entry(model, refs, preds, n_items=len(results)))
    refs = [r.item.reference for r in results]
    preds = [r.verdict.technique for r in results]
    out.append(_model_entry(ENSEMBLE, refs, preds, n_items=len(results)))
    return out


def _model_entry(model: str, refs: list[str], preds: list[str], n_items: int) -> dict:
    return {
        "model": model,
        "n_valid_votes": len(refs),
        "n_missing_votes": n_items - len(refs),
        "accuracy": proportion(sum(r == p for r, p in zip(refs, preds, strict=True)), len(refs)),
        "macro_f1": _round(macro_f1_value(refs, preds)[0]),
        "fpr_no_technique": fpr_no_technique(refs, preds),
    }


def source_metrics(
    results: Sequence[ItemResult],
    *,
    label: str,
    excluded: dict | None,
    models: Sequence[str] | None = None,
) -> dict:
    """All metrics for one source. Failed items are counted in ``n_errors``, not scored."""
    ok = [r for r in results if r.ok]
    errors = [r for r in results if not r.ok]
    if models is None:
        models = model_ids(ok)
    refs = [r.item.reference for r in ok]
    preds = [r.verdict.technique for r in ok]
    n_models = max((r.verdict.n_models for r in ok), default=3)
    core = classification_metrics(refs, preds)
    return {
        "label": label,
        "n": len(ok),
        "n_total": len(results),
        "n_errors": len(errors),
        "error_ids": [r.item.id for r in errors],
        "n_per_technique": {t: refs.count(t) for t in TECHNIQUES},
        "n_excluded_out_of_taxonomy": (excluded or {}).get("out_of_taxonomy_only", 0),
        "excluded": excluded,
        "accuracy": core["accuracy"],
        "macro_f1": core["macro_f1"],
        "fpr_no_technique": fpr_no_technique(refs, preds),
        "per_technique": core["per_technique"],
        "confusion_matrix": core["confusion_matrix"],
        "span_token_f1": span_token_f1(ok),
        "by_agreement": by_agreement(ok, n_models=n_models),
        "flag_policies": flag_policies(ok),
        "per_model": per_model(ok, models),
        "human_ceiling": None,
    }
