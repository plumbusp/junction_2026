"""Headline and round scoring: 60 for the technique plus 40 x token-F1 for the evidence words.

Pure functions, standard library only. `token_f1` is also imported by backend/eval.

Arithmetic is done with `fractions.Fraction` so that "halves rounded up" is exact and
never depends on float representation (for example 40 * 1/16 = 2.5 must round to 3).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from fractions import Fraction

from app.core.types import TECHNIQUES, Verdict

TECHNIQUE_POINTS = 60
EVIDENCE_POINTS = 40
NO_TECHNIQUE = "no_technique"

# A token is a run of letters and digits. Hyphens (ASCII and the two Unicode hyphens)
# between two such runs keep a compound such as "rip-off" as one token; every other
# non-letter, non-digit character splits. `[^\W_]` is "word character minus underscore",
# i.e. a Unicode letter or digit.
_TOKEN_RE = re.compile(r"[^\W_]+(?:[-‐‑][^\W_]+)*")


@dataclass(frozen=True)
class HeadlineScore:
    technique_points: int  # 60 or 0
    evidence_points: int  # 0..40
    total: int  # technique_points + evidence_points, 0..100
    evidence_f1: (
        float  # token-F1 used for the evidence points (1.0 under the special case)
    )


def tokenize(text: str) -> list[str]:
    """Lowercase `text` and split it into letter/digit tokens (hyphenated compounds kept whole)."""
    return _TOKEN_RE.findall(text.lower())


def _tokens(items: Iterable[str] | str) -> list[str]:
    if isinstance(items, str):
        items = [items]
    tokens: list[str] = []
    for item in items:
        tokens.extend(tokenize(item))
    return tokens


def _token_f1_exact(
    predicted: Iterable[str] | str, reference: Iterable[str] | str
) -> Fraction:
    pred = _tokens(predicted)
    ref = _tokens(reference)
    if not pred and not ref:
        return Fraction(1)
    if not pred or not ref:
        return Fraction(0)
    overlap = sum((Counter(pred) & Counter(ref)).values())
    if overlap == 0:
        return Fraction(0)
    # F1 = 2PR / (P + R) with P = o/|pred|, R = o/|ref| simplifies to 2o / (|pred| + |ref|).
    return Fraction(2 * overlap, len(pred) + len(ref))


def token_f1(predicted: Iterable[str] | str, reference: Iterable[str] | str) -> float:
    """Multiset token-F1 between two lists of words/spans (or two strings).

    Both empty gives 1.0; exactly one side empty gives 0.0.
    """
    return float(_token_f1_exact(predicted, reference))


def round_half_up(value: Fraction | float) -> int:
    """Round to the nearest integer, halves up (2.5 -> 3, unlike Python's banker's rounding)."""
    exact = value if isinstance(value, Fraction) else Fraction(value)
    return math.floor(exact + Fraction(1, 2))


def score_headline(
    player_technique: str,
    player_words: Sequence[str],
    reference: Verdict,
) -> HeadlineScore:
    """Score one headline against the reference verdict.

    Evidence is scored independently of the technique choice. Special case: reference
    `no_technique` and no words selected gives the full 40 evidence points.
    """
    if player_technique not in TECHNIQUES:
        raise ValueError(
            f"unknown technique {player_technique!r}; expected one of {TECHNIQUES}"
        )

    technique_points = (
        TECHNIQUE_POINTS if player_technique == reference.technique else 0
    )

    if reference.technique == NO_TECHNIQUE and not _tokens(player_words):
        f1 = Fraction(1)
    else:
        f1 = _token_f1_exact(player_words, reference.evidence_spans)
    evidence_points = round_half_up(EVIDENCE_POINTS * f1)

    return HeadlineScore(
        technique_points=technique_points,
        evidence_points=evidence_points,
        total=technique_points + evidence_points,
        evidence_f1=float(f1),
    )


def score_round(headline_totals: Sequence[int | HeadlineScore]) -> int:
    """Round score: mean of the headline totals, rounded to the nearest integer, halves up."""
    if not headline_totals:
        raise ValueError("a round needs at least one headline score")
    totals = [h.total if isinstance(h, HeadlineScore) else h for h in headline_totals]
    return round_half_up(Fraction(sum(totals), len(totals)))
