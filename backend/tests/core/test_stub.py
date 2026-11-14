"""Stub mode: deterministic, real types, valid spans, no network."""

from __future__ import annotations

import httpx
import pytest
from app.config import get_settings
from app.core.classifier import classify
from app.core.stub import is_stub_verdict, stub_classify
from app.core.types import TECHNIQUES, Verdict

HEADLINES = [
    "Government unveils budget plan for 2027",
    "Mayor slams rip-off parking fees",
    "Experts say the crisis will destroy everything",
    "What about the opposition's own scandals?",
    "Local library extends opening hours",
    "Can we really trust the so-called report?",
]


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("stub mode must not use the network")

    monkeypatch.setattr(httpx.Client, "send", refuse)


def test_tests_run_in_stub_mode() -> None:
    assert get_settings().core_mode == "stub"


def test_stub_is_deterministic_and_well_formed() -> None:
    for text in HEADLINES:
        first, second = classify(text), classify(text)

        assert first == second
        assert isinstance(first, Verdict)
        assert first.technique in TECHNIQUES
        assert all(span in text for span in first.evidence_spans)
        assert first.disputed == (first.agreement < 2)
        assert is_stub_verdict(first)


def test_stub_produces_varied_verdicts_for_consumers() -> None:
    verdicts = [
        stub_classify(f"Headline number {i} about the city council") for i in range(60)
    ]

    assert len({v.technique for v in verdicts}) >= 3
    assert any(v.disputed for v in verdicts)
    assert any(v.agreement == 3 for v in verdicts)
