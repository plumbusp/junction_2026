"""Lineup builder: three headlines from three distinct outlets, as varied as possible."""

from __future__ import annotations

import random

import pytest
from app.game.rounds import LineupHeadline, build_lineup

from tests.db.rounds_fixture import stub_verdict


def head(
    n: int,
    outlet: str,
    technique: str,
    *,
    story: str | None = None,
    leaning: str = "center",
    published: float = 1000.0,
    disputed: bool = False,
) -> LineupHeadline:
    ref = stub_verdict(technique, [] if technique == "no_technique" else ["x"], disputed=disputed)
    return LineupHeadline(
        id=f"h{n}",
        story_id=story or f"s{n}",
        outlet=outlet,
        text=f"Invented headline {n}",
        reference=ref,
        leaning=leaning,
        published=published,
    )


def varied_pool() -> list[LineupHeadline]:
    return [
        head(1, "a", "no_technique"),
        head(2, "a", "no_technique"),
        head(3, "b", "no_technique"),
        head(4, "b", "doubt"),
        head(5, "c", "doubt"),
        head(6, "c", "loaded_language"),
        head(7, "d", "loaded_language"),
        head(8, "d", "appeal_to_fear"),
        head(9, "e", "no_technique"),
    ]


def test_lineup_uses_distinct_outlets_and_techniques_and_includes_a_clean_one() -> None:
    lineup = build_lineup(varied_pool(), rng_seed="d1")

    assert lineup is not None
    assert len({h.outlet for h in lineup.headlines}) == 3
    assert lineup.diversity == 3
    assert any(h.is_clean for h in lineup.headlines)


def test_same_pool_and_seed_give_the_same_lineup_in_any_input_order() -> None:
    pool = varied_pool()
    shuffled = pool[:]
    random.Random(1).shuffle(shuffled)

    first = build_lineup(pool, rng_seed="d1")
    second = build_lineup(shuffled, rng_seed="d1")

    assert first is not None and second is not None
    assert {h.id for h in first.headlines} == {h.id for h in second.headlines}
    assert first.headlines == second.headlines


def test_novelty_breaks_a_tie_between_equally_diverse_lineups() -> None:
    pool = [
        head(1, "a", "no_technique"),
        head(2, "b", "doubt"),
        head(3, "c", "loaded_language"),
        head(4, "d", "appeal_to_fear"),
    ]

    fresh = build_lineup(pool, used_techniques={"doubt", "loaded_language"}, rng_seed="x")
    stale = build_lineup(pool, used_techniques={"appeal_to_fear"}, rng_seed="x")

    assert fresh is not None and stale is not None
    assert "appeal_to_fear" in fresh.techniques
    assert "appeal_to_fear" not in stale.techniques


def test_more_declared_leanings_win_when_techniques_and_novelty_tie() -> None:
    pool = [
        head(1, "a", "no_technique", leaning="left"),
        head(2, "b", "doubt", leaning="left"),
        head(3, "c", "loaded_language", leaning="left"),
        head(4, "d", "loaded_language", leaning="right"),
    ]

    lineup = build_lineup(pool, rng_seed="x")

    assert lineup is not None
    assert {h.id for h in lineup.headlines} == {"h1", "h2", "h4"}


def test_freshest_headlines_win_the_last_content_tie() -> None:
    pool = [
        head(1, "a", "no_technique"),
        head(2, "b", "doubt"),
        head(3, "c", "loaded_language", published=1.0),
        head(4, "d", "loaded_language", published=9000.0),
    ]

    lineup = build_lineup(pool, rng_seed="x")

    assert lineup is not None
    assert "h4" in {h.id for h in lineup.headlines}


def test_different_stories_are_preferred_over_one_story() -> None:
    pool = [
        head(1, "a", "no_technique", story="one"),
        head(2, "b", "doubt", story="one"),
        head(3, "c", "loaded_language", story="two"),
        head(5, "e", "appeal_to_fear", story="three"),
    ]

    lineup = build_lineup(pool, rng_seed="x")

    assert lineup is not None and lineup.distinct_stories
    assert "h2" not in {h.id for h in lineup.headlines}


def test_fewer_than_two_non_clean_headlines_gives_a_lower_diversity_lineup() -> None:
    pool = [
        head(1, "a", "no_technique"),
        head(2, "b", "no_technique"),
        head(3, "c", "doubt"),
    ]

    lineup = build_lineup(pool, rng_seed="x")

    assert lineup is not None and lineup.diversity == 2

    flat = build_lineup([head(n, o, "no_technique") for n, o in enumerate("abc")], rng_seed="x")
    assert flat is not None and flat.diversity == 1


def test_two_headlines_never_share_a_technique_when_an_alternative_exists() -> None:
    pool = [
        head(1, "a", "no_technique"),
        head(2, "b", "doubt"),
        head(3, "c", "doubt"),
        head(4, "d", "doubt"),
        head(5, "e", "appeal_to_fear"),
    ]

    lineup = build_lineup(pool, rng_seed="x")

    assert lineup is not None
    assert sorted(h.reference.technique for h in lineup.headlines) == [
        "appeal_to_fear",
        "doubt",
        "no_technique",
    ]


def test_no_lineup_without_three_outlets_or_without_undisputed_headlines() -> None:
    two_outlets = [
        head(1, "a", "no_technique"),
        head(2, "a", "doubt"),
        head(3, "b", "loaded_language"),
    ]
    one_disputed = [
        head(1, "a", "no_technique"),
        head(2, "b", "doubt"),
        head(3, "c", "doubt", disputed=True),
    ]

    assert build_lineup(two_outlets, rng_seed="x") is None
    assert build_lineup(one_disputed, rng_seed="x") is None
    assert build_lineup([], rng_seed="x") is None


def test_without_any_clean_headline_only_three_distinct_techniques_qualify() -> None:
    same = [head(1, "a", "doubt"), head(2, "b", "doubt"), head(3, "c", "loaded_language")]
    distinct = [head(1, "a", "doubt"), head(2, "b", "appeal_to_fear"), head(3, "c", "loaded_language")]

    assert build_lineup(same, rng_seed="x") is None
    lineup = build_lineup(distinct, rng_seed="x")
    assert lineup is not None and lineup.diversity == 3


def test_duplicate_ids_are_rejected() -> None:
    with pytest.raises(ValueError):
        build_lineup([head(1, "a", "doubt"), head(1, "b", "doubt")], rng_seed="x")
