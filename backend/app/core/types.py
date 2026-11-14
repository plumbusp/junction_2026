"""Core contract types, as defined in docs/ARCHITECTURE.md ("Core contract")."""

from __future__ import annotations

from dataclasses import dataclass

TECHNIQUES = [
    "loaded_language",
    "appeal_to_fear",
    "exaggeration_minimization",
    "doubt",
    "vague_authority",
    "changing_subject",
    "no_technique",
]


def _check_technique(technique: str) -> None:
    if technique not in TECHNIQUES:
        raise ValueError(f"unknown technique {technique!r}; expected one of {TECHNIQUES}")


@dataclass
class ModelVote:
    model: str  # e.g. "claude", "gpt", "deepseek"
    technique: str  # one of TECHNIQUES
    evidence_spans: list[str]  # exact substrings of the input text
    explanation: str

    def __post_init__(self) -> None:
        _check_technique(self.technique)


@dataclass
class Verdict:
    technique: str  # majority vote; "no_technique" if no majority
    evidence_spans: list[str]  # spans chosen by >= 2 models
    explanation: str  # plain language, from a model that voted with the majority
    agreement: int  # how many models voted for `technique` (0..3)
    n_models: int  # usually 3
    disputed: bool  # True when agreement < 2
    votes: list[ModelVote]

    def __post_init__(self) -> None:
        _check_technique(self.technique)
