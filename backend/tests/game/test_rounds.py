"""Round assembly: three headlines of one event, most distinct techniques, a clean one when possible."""

from __future__ import annotations

import pytest
from app.core.types import Verdict
from app.game.rounds import Headline, build_round


def _headline(hid: str, event_id: str, technique: str, source: str = "") -> Headline:
    verdict = Verdict(
        technique=technique,
        evidence_spans=[],
        explanation="",
        agreement=3,
        n_models=3,
        disputed=False,
        votes=[],
    )
    return Headline(
        id=hid,
        event_id=event_id,
        text=f"headline {hid}",
        reference=verdict,
        source=source,
    )


POOL = [
    _headline("a1", "event-a", "loaded_language"),
    _headline("a2", "event-a", "appeal_to_fear"),
    _headline("a3", "event-a", "no_technique"),
    _headline("a4", "event-a", "doubt"),
    _headline("a5", "event-a", "vague_authority"),
    _headline("b1", "event-b", "doubt"),
    _headline("b2", "event-b", "no_technique"),
    _headline("b3", "event-b", "no_technique"),
    # event-c has no clean headline but three distinct techniques: eligible.
    _headline("c1", "event-c", "doubt"),
    _headline("c2", "event-c", "loaded_language"),
    _headline("c3", "event-c", "appeal_to_fear"),
    # event-d has too few headlines: never chosen.
    _headline("d1", "event-d", "no_technique"),
    _headline("d2", "event-d", "doubt"),
]


def _ids(seed: int, pool: list[Headline]) -> tuple[str, ...]:
    return tuple(h.id for h in build_round(pool, seed).headlines)


def test_round_has_three_distinct_techniques_of_one_event_and_a_clean_one_if_any() -> (
    None
):
    for seed in range(50):
        rnd = build_round(POOL, seed)

        assert len({h.id for h in rnd.headlines}) == 3
        assert {h.event_id for h in rnd.headlines} == {rnd.event_id}
        # event-b can only reach 2 techniques, so the 3-technique events always win.
        assert rnd.event_id in {"event-a", "event-c"}
        assert rnd.diversity == 3 and len(rnd.techniques) == 3
        if rnd.event_id == "event-a":
            assert any(h.reference.technique == "no_technique" for h in rnd.headlines)


def test_subset_maximizes_distinct_techniques_and_keeps_a_clean_headline() -> None:
    pool = [
        _headline("e1", "e", "no_technique"),
        _headline("e2", "e", "no_technique"),
        _headline("e3", "e", "no_technique"),
        _headline("e4", "e", "loaded_language"),
        _headline("e5", "e", "loaded_language"),
        _headline("e6", "e", "doubt"),
    ]

    for seed in range(30):
        rnd = build_round(pool, seed)

        assert rnd.techniques == ("doubt", "loaded_language", "no_technique")
        assert rnd.diversity == 3


def test_more_distinct_outlets_break_a_tie_on_techniques() -> None:
    pool = [
        _headline("e1", "e", "no_technique", "A"),
        _headline("e2", "e", "loaded_language", "A"),
        _headline("e3", "e", "loaded_language", "B"),
        _headline("e4", "e", "doubt", "A"),
    ]

    for seed in range(20):
        sources = {h.source for h in build_round(pool, seed).headlines}
        assert sources == {"A", "B"}


def test_same_seed_gives_same_round_regardless_of_pool_order() -> None:
    first = _ids(42, POOL)

    assert _ids(42, POOL) == first
    assert _ids(42, list(reversed(POOL))) == first
    assert (
        len({_ids(seed, POOL) for seed in range(50)}) > 1
    )  # the seed actually matters


def test_low_diversity_is_a_last_resort_and_reported() -> None:
    all_clean = [_headline(f"x{i}", "x", "no_technique") for i in range(3)]

    assert build_round(all_clean, 1).diversity == 1
    # Given a better event, the 1-technique event is never chosen.
    mixed = all_clean + [h for h in POOL if h.event_id == "event-a"]
    assert {build_round(mixed, s).event_id for s in range(20)} == {"event-a"}


def test_pool_without_eligible_event_raises() -> None:
    no_clean_repeating = [
        _headline("c1", "c", "doubt"),
        _headline("c2", "c", "doubt"),
        _headline("c3", "c", "loaded_language"),
    ]

    with pytest.raises(ValueError, match="no event"):
        build_round(no_clean_repeating, 1)  # no clean headline, only 2 techniques
    with pytest.raises(ValueError, match="no event"):
        build_round([h for h in POOL if h.event_id == "event-d"], 1)
    with pytest.raises(ValueError, match="duplicate"):
        build_round(POOL + [POOL[0]], 1)
