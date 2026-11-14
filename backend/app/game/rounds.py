"""Round assembly: three headlines about one event, at least one of them clean.

Pure and deterministic: the API passes the pool in, the same pool and seed always give
the same round, and the order of the pool does not matter.
"""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from app.core.types import Verdict

HEADLINES_PER_ROUND = 3
NO_TECHNIQUE = "no_technique"


@dataclass(frozen=True)
class Headline:
    id: str  # unique within the pool
    event_id: str  # headlines with the same event_id are about the same event
    text: str
    reference: Verdict
    source: str = ""  # outlet name, informational only

    @property
    def is_clean(self) -> bool:
        return self.reference.technique == NO_TECHNIQUE


@dataclass(frozen=True)
class Round:
    event_id: str
    headlines: tuple[Headline, Headline, Headline]


def _eligible_events(pool: Iterable[Headline]) -> dict[str, list[Headline]]:
    by_event: dict[str, list[Headline]] = defaultdict(list)
    seen_ids: set[str] = set()
    for headline in pool:
        if headline.id in seen_ids:
            raise ValueError(f"duplicate headline id {headline.id!r} in pool")
        seen_ids.add(headline.id)
        by_event[headline.event_id].append(headline)
    return {
        event_id: sorted(items, key=lambda h: h.id)
        for event_id, items in by_event.items()
        if len(items) >= HEADLINES_PER_ROUND and any(h.is_clean for h in items)
    }


def build_round(pool: Iterable[Headline], rng_seed: int | str) -> Round:
    """Pick one event and three of its headlines, at least one with reference `no_technique`.

    Raises ValueError if no event in the pool has three headlines including a clean one.
    """
    events = _eligible_events(pool)
    if not events:
        raise ValueError(
            "no event in the pool has at least three headlines including a no_technique one"
        )

    rng = random.Random(rng_seed)
    event_id = rng.choice(sorted(events))
    candidates = events[event_id]

    clean = rng.choice([h for h in candidates if h.is_clean])
    others = rng.sample(
        [h for h in candidates if h.id != clean.id], HEADLINES_PER_ROUND - 1
    )
    chosen = [clean, *others]
    rng.shuffle(chosen)  # the clean headline must not always sit in the same slot
    return Round(event_id=event_id, headlines=(chosen[0], chosen[1], chosen[2]))
