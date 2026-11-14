"""Round assembly: three headlines about one event, at least one clean, deterministic per seed."""

from __future__ import annotations

import pytest
from app.core.types import Verdict
from app.game.rounds import Headline, build_round


def _headline(hid: str, event_id: str, technique: str) -> Headline:
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
        id=hid, event_id=event_id, text=f"headline {hid}", reference=verdict
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
    # event-c has no clean headline, event-d has too few headlines: both are never chosen.
    _headline("c1", "event-c", "doubt"),
    _headline("c2", "event-c", "loaded_language"),
    _headline("c3", "event-c", "appeal_to_fear"),
    _headline("d1", "event-d", "no_technique"),
    _headline("d2", "event-d", "doubt"),
]


def _ids(seed: int, pool: list[Headline]) -> tuple[str, ...]:
    return tuple(h.id for h in build_round(pool, seed).headlines)


def test_round_has_three_headlines_of_one_event_with_a_clean_one() -> None:
    for seed in range(50):
        rnd = build_round(POOL, seed)

        assert len(rnd.headlines) == 3
        assert len({h.id for h in rnd.headlines}) == 3
        assert {h.event_id for h in rnd.headlines} == {rnd.event_id}
        assert rnd.event_id in {"event-a", "event-b"}
        assert any(h.reference.technique == "no_technique" for h in rnd.headlines)


def test_same_seed_gives_same_round_regardless_of_pool_order() -> None:
    first = _ids(42, POOL)

    assert _ids(42, POOL) == first
    assert _ids(42, list(reversed(POOL))) == first
    assert (
        len({_ids(seed, POOL) for seed in range(50)}) > 1
    )  # the seed actually matters


def test_pool_without_eligible_event_raises() -> None:
    no_clean = [h for h in POOL if h.event_id == "event-c"]

    with pytest.raises(ValueError, match="no event"):
        build_round(no_clean, 1)
    with pytest.raises(ValueError, match="duplicate"):
        build_round(POOL + [POOL[0]], 1)
