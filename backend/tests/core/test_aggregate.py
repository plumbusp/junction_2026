"""Pure aggregation rules: majority, disputed, span agreement, span validation."""

from __future__ import annotations

from app.core.aggregate import DISAGREEMENT_EXPLANATION, aggregate, validate_spans
from app.core.types import ModelVote


def vote(
    model: str, technique: str, spans: list[str], explanation: str = ""
) -> ModelVote:
    return ModelVote(
        model=model, technique=technique, evidence_spans=spans, explanation=explanation
    )


def test_majority_wins_with_spans_shared_by_two_majority_models() -> None:
    votes = [
        vote("a", "loaded_language", ["slams", "rip-off"], "Charged words."),
        vote("b", "loaded_language", ["slams"], "Emotional verb."),
        vote("c", "appeal_to_fear", ["rip-off"], "Fear."),
    ]

    verdict = aggregate(votes, n_models=3, min_accuse=2)

    assert verdict.technique == "loaded_language"
    assert verdict.agreement == 2
    assert verdict.disputed is False
    # "rip-off" has two votes overall but only one from the majority, so it is dropped.
    assert verdict.evidence_spans == ["slams"]
    assert verdict.explanation == "Charged words."
    assert verdict.votes == votes


def test_no_majority_gives_no_technique_and_disputed() -> None:
    votes = [
        vote("a", "loaded_language", ["slams"], "A says loaded."),
        vote("b", "doubt", [], "B says doubt."),
        vote("c", "vague_authority", [], "C says authority."),
    ]

    verdict = aggregate(votes, n_models=3)

    assert verdict.technique == "no_technique"
    assert verdict.disputed is True
    assert verdict.agreement == 0
    assert verdict.evidence_spans == []
    assert (
        verdict.explanation == DISAGREEMENT_EXPLANATION
    )  # no single model's explanation


def test_fewer_than_two_votes_is_disputed() -> None:
    one = aggregate([vote("a", "doubt", [], "Only one.")], n_models=3)
    none = aggregate([], n_models=3)

    assert (one.technique, one.disputed, one.n_models) == ("no_technique", True, 3)
    assert (none.technique, none.disputed, none.agreement) == ("no_technique", True, 0)


def test_validate_spans_drops_invented_and_altered_spans() -> None:
    text = "Experts say the new tax is a rip-off"

    kept = validate_spans(
        text, ["Experts say", "experts say", "a ripoff", "", "rip-off", "rip-off"]
    )

    assert kept == ["Experts say", "rip-off"]


def test_two_of_three_is_not_enough_to_accuse_by_default() -> None:
    votes = [
        vote("a", "loaded_language", ["slams"], "Charged words."),
        vote("b", "loaded_language", ["slams"], "Emotional verb."),
        vote("c", "no_technique", [], "Neutral."),
    ]

    verdict = aggregate(votes, n_models=3)

    assert (verdict.technique, verdict.disputed, verdict.agreement) == ("no_technique", True, 1)
    assert verdict.evidence_spans == []
    assert "2 of 3 models saw loaded language" in verdict.explanation
    assert verdict.votes == votes


def test_three_of_three_accuses() -> None:
    votes = [vote(m, "doubt", ["really?"], "Doubt.") for m in "abc"]

    verdict = aggregate(votes, n_models=3)

    assert (verdict.technique, verdict.agreement, verdict.disputed) == ("doubt", 3, False)
    assert verdict.evidence_spans == ["really?"]


def test_two_of_three_saying_clean_is_a_clean_verdict() -> None:
    votes = [
        vote("a", "no_technique", [], "Neutral."),
        vote("b", "no_technique", [], "Neutral."),
        vote("c", "doubt", [], "Doubt."),
    ]

    verdict = aggregate(votes, n_models=3)

    assert (verdict.technique, verdict.agreement, verdict.disputed) == ("no_technique", 2, False)
