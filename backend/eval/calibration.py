"""Agreement calibration and the false-positive trade-off between two flagging rules.

Agreement is the share of models that voted for the final technique; it is not a
probability. If it is a useful confidence signal, accuracy drops from 3/3 to 2/3 to disputed.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from eval.wilson import proportion

if TYPE_CHECKING:
    from eval.metrics import ItemResult

NO_TECHNIQUE = "no_technique"
DISPUTED = "disputed"

# Policy id, label for the page, minimum agreement for a technique verdict to be flagged.
FLAG_POLICIES = [
    ("flag_at_2_of_3", "Flag at 2 of 3 or more", 2),
    ("flag_at_3_of_3", "Flag only at 3 of 3 (default)", 3),
]


def agreement_level(result: ItemResult) -> str:
    """``"3/3"``, ``"2/3"`` (agreement/n_models) or ``"disputed"``."""
    verdict = result.verdict
    if verdict.disputed:
        return DISPUTED
    return f"{verdict.agreement}/{verdict.n_models}"


def by_agreement(results: Sequence[ItemResult], n_models: int = 3) -> list[dict]:
    """Accuracy per agreement level. "3/3", "2/3" and "disputed" are always present.

    Disputed verdicts carry technique ``no_technique``, so a disputed item counts as
    correct only when its reference is ``no_technique``.
    """
    groups: dict[str, list[bool]] = {
        f"{n_models}/{n_models}": [],
        f"2/{n_models}": [],
        DISPUTED: [],
    }
    for result in results:
        groups.setdefault(agreement_level(result), []).append(result.correct)

    def order(level: str) -> tuple[int, int]:
        if level == DISPUTED:
            return (1, 0)
        return (0, -int(level.split("/")[0]))

    return [
        {"level": level, "n": len(hits), "accuracy": proportion(sum(hits), len(hits))}
        for level, hits in sorted(groups.items(), key=lambda kv: order(kv[0]))
    ]


def flagged_technique(result: ItemResult, min_agreement: int) -> str | None:
    """The technique a rule would flag: the top vote, if it is a technique with at least
    ``min_agreement`` votes and no tie. Computed from the raw votes, so every rule can be
    measured whatever rule the live ensemble uses."""
    counts: dict[str, int] = {}
    for vote in result.verdict.votes:
        counts[vote.technique] = counts.get(vote.technique, 0) + 1
    if not counts:
        return None
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    label, top = ranked[0]
    if len(ranked) > 1 and ranked[1][1] == top:
        return None
    if label == NO_TECHNIQUE or top < max(2, min_agreement):
        return None
    return label


def is_flagged(result: ItemResult, min_agreement: int) -> bool:
    """True when the rule flags any technique for this text."""
    return flagged_technique(result, min_agreement) is not None


def flag_policies(results: Sequence[ItemResult]) -> list[dict]:
    """FPR on no_technique and recall of technique detection under each flagging rule.

    - fpr_no_technique: share of ``no_technique`` references that were flagged;
    - recall_any_technique: share of technique references flagged with any technique;
    - recall_correct_technique: share of technique references flagged with the right one.
    """
    clean = [r for r in results if r.item.reference == NO_TECHNIQUE]
    manipulated = [r for r in results if r.item.reference != NO_TECHNIQUE]
    out = []
    for policy, label, min_agreement in FLAG_POLICIES:
        flagged_clean = sum(is_flagged(r, min_agreement) for r in clean)
        flagged_any = sum(is_flagged(r, min_agreement) for r in manipulated)
        flagged_right = sum(
            flagged_technique(r, min_agreement) == r.item.reference
            for r in manipulated
        )
        out.append(
            {
                "policy": policy,
                "label": label,
                "min_agreement": min_agreement,
                "fpr_no_technique": proportion(flagged_clean, len(clean)),
                "recall_any_technique": proportion(flagged_any, len(manipulated)),
                "recall_correct_technique": proportion(flagged_right, len(manipulated)),
            }
        )
    return out
