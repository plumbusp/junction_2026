"""Scoring rules: 60 for the technique + 40 x token-F1 for the evidence, halves rounded up."""

from __future__ import annotations

from fractions import Fraction

import pytest
from app.core.types import Verdict
from app.game.scoring import round_half_up, score_headline, score_round, token_f1


def _verdict(technique: str, spans: list[str]) -> Verdict:
    return Verdict(
        technique=technique,
        evidence_spans=spans,
        explanation="",
        agreement=3,
        n_models=3,
        disputed=False,
        votes=[],
    )


def test_worked_example_87_27_100_round_71() -> None:
    a = score_headline(
        "loaded_language", ["slams"], _verdict("loaded_language", ["slams", "rip-off"])
    )
    b = score_headline(
        "doubt", ["drown"], _verdict("appeal_to_fear", ["drown", "gridlock"])
    )
    c = score_headline("no_technique", [], _verdict("no_technique", []))

    assert (a.technique_points, a.evidence_points, a.total) == (60, 27, 87)
    assert a.evidence_f1 == pytest.approx(2 / 3)
    assert (b.technique_points, b.evidence_points, b.total) == (0, 27, 27)
    assert (c.technique_points, c.evidence_points, c.total) == (60, 40, 100)
    assert score_round([a, b, c]) == 71
    assert score_round([87, 27, 100]) == 71


def test_no_technique_reference_and_no_words_gives_full_evidence_even_if_technique_wrong() -> (
    None
):
    score = score_headline("doubt", [], _verdict("no_technique", []))

    assert (score.technique_points, score.evidence_points, score.total) == (0, 40, 40)


def test_words_selected_on_no_technique_reference_give_zero_evidence() -> None:
    score = score_headline("no_technique", ["calm"], _verdict("no_technique", []))

    assert (score.technique_points, score.evidence_points, score.total) == (60, 0, 60)


def test_wrong_technique_still_earns_evidence_points() -> None:
    score = score_headline(
        "doubt",
        ["drown", "gridlock"],
        _verdict("appeal_to_fear", ["drown", "gridlock"]),
    )

    assert (score.technique_points, score.evidence_points, score.total) == (0, 40, 40)


def test_halves_round_up() -> None:
    # One player token against 31 reference tokens with one overlap: F1 = 2/32, 40 * F1 = 2.5.
    reference = _verdict("loaded_language", ["slams"] + [f"w{i}" for i in range(30)])
    score = score_headline("loaded_language", ["slams"], reference)

    assert score.evidence_points == 3
    assert round_half_up(Fraction(5, 2)) == 3
    assert round_half_up(0.5) == 1
    assert round_half_up(Fraction(7, 3)) == 2
    assert score_round([50, 51]) == 51  # mean 50.5 rounds up


@pytest.mark.parametrize(
    ("player", "reference", "expected"),
    [
        ([], [], 1.0),
        ([], ["slams"], 0.0),
        (["slams"], [], 0.0),
        (["Slams!"], ["slams"], 1.0),  # lowercased, punctuation split off
        (
            ["very", "very"],
            ["very"],
            pytest.approx(2 / 3),
        ),  # multiset: overlap is 1, not 2
        (["rip-off"], ["rip"], 0.0),  # hyphenated compound is one token
    ],
)
def test_token_f1_edge_cases(
    player: list[str], reference: list[str], expected: float
) -> None:
    assert token_f1(player, reference) == expected


def test_invalid_inputs_raise() -> None:
    with pytest.raises(ValueError, match="unknown technique"):
        score_headline("propaganda", [], _verdict("no_technique", []))
    with pytest.raises(ValueError, match="at least one"):
        score_round([])
