"""Deterministic fake verdicts for CORE_MODE=stub (tests and parallel development).

No network, no key. The same text always gives the same Verdict. Fake votes go through
the real ``aggregate`` so stub verdicts obey the same rules as live ones. Stub votes use
model names starting with ``STUB_MODEL_PREFIX``; results marked as real must reject them
(see ``is_stub_verdict``).
"""

from __future__ import annotations

import hashlib
import re

from app.core.aggregate import aggregate
from app.core.cache import normalize_text
from app.core.types import TECHNIQUES, ModelVote, Verdict

STUB_MODEL_PREFIX = "stub-"
STUB_MODELS = [
    f"{STUB_MODEL_PREFIX}a",
    f"{STUB_MODEL_PREFIX}b",
    f"{STUB_MODEL_PREFIX}c",
]

_WORD = re.compile(r"\w[\w'-]*")
_REAL_TECHNIQUES = [t for t in TECHNIQUES if t != "no_technique"]


def _explanation(technique: str) -> str:
    return (
        f"Stub verdict (not a real model): deterministic placeholder for {technique}."
    )


def stub_votes(text: str) -> list[ModelVote]:
    """Three deterministic fake votes for ``text``.

    Roughly: 60% unanimous, 30% two of three, 10% full disagreement (disputed).
    """
    digest = hashlib.sha256(normalize_text(text).encode("utf-8")).digest()
    words = [m.group() for m in _WORD.finditer(text)]

    technique = TECHNIQUES[digest[0] % len(TECHNIQUES)] if words else "no_technique"
    spans = [] if technique == "no_technique" else [words[digest[1] % len(words)]]
    pattern = digest[2] % 10

    def vote(model: str, label: str) -> ModelVote:
        return ModelVote(
            model=model,
            technique=label,
            evidence_spans=list(spans) if label == technique else [],
            explanation=_explanation(label),
        )

    if pattern < 6:  # 3 of 3
        labels = [technique, technique, technique]
    elif pattern < 9:  # 2 of 3
        other = (
            "no_technique"
            if technique != "no_technique"
            else _REAL_TECHNIQUES[digest[3] % 6]
        )
        labels = [technique, technique, other]
    else:  # no majority
        start = digest[3] % len(TECHNIQUES)
        labels = [TECHNIQUES[(start + i) % len(TECHNIQUES)] for i in range(3)]
        spans = []
    return [
        vote(model, label) for model, label in zip(STUB_MODELS, labels, strict=True)
    ]


def stub_classify(text: str) -> Verdict:
    return aggregate(stub_votes(text), n_models=len(STUB_MODELS))


def is_stub_verdict(verdict: Verdict) -> bool:
    """True if any vote came from the stub. Such verdicts must never be reported as real."""
    return any(vote.model.startswith(STUB_MODEL_PREFIX) for vote in verdict.votes)
