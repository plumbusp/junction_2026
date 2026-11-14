"""Round assembly: three headlines about one event, as different from each other as possible.

The round must give players a technique to tell apart, so among the labelled headlines of an
event the builder picks the 3-subset with the most DISTINCT reference techniques
(``no_technique`` counts as one value). Whenever the event has a clean headline the round
contains one (players always get a headline to recognise as clean); an event without a clean
headline qualifies only if it can show three distinct techniques. The builder never relabels
anything: it only chooses among the labels it is given, and reports how diverse the result is.

Pure and deterministic: the API passes the pool in, the same pool and seed always give
the same round, and the order of the pool does not matter.
"""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import combinations

from app.core.types import Verdict

HEADLINES_PER_ROUND = 3
NO_TECHNIQUE = "no_technique"


@dataclass(frozen=True)
class Headline:
    id: str  # unique within the pool
    event_id: str  # headlines with the same event_id are about the same event
    text: str
    reference: Verdict
    source: str = ""  # outlet name, informational (and a tie-break: more outlets win)

    @property
    def is_clean(self) -> bool:
        return self.reference.technique == NO_TECHNIQUE


@dataclass(frozen=True)
class Round:
    event_id: str
    headlines: tuple[Headline, Headline, Headline]

    @property
    def techniques(self) -> tuple[str, ...]:
        """Sorted distinct reference techniques of the three headlines (internal only)."""
        return tuple(sorted({h.reference.technique for h in self.headlines}))

    @property
    def diversity(self) -> int:
        """Number of distinct reference techniques among the three headlines (1..3)."""
        return len(self.techniques)


def _group_by_event(pool: Iterable[Headline]) -> dict[str, list[Headline]]:
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
    }


def _best_subsets(
    items: list[Headline],
) -> tuple[int, list[tuple[Headline, ...]]]:
    """The best-scoring 3-subsets of one event as ``(diversity, tied subsets)``.

    Score: distinct techniques, then distinct outlets. A subset must contain a clean
    headline when the event has one; with no clean headline only three distinct techniques
    qualify. Returns ``(0, [])`` for an event that cannot form a round.
    """
    if len(items) < HEADLINES_PER_ROUND:
        return 0, []
    has_clean = any(h.is_clean for h in items)
    best_score: tuple[int, int] = (0, 0)
    best: list[tuple[Headline, ...]] = []
    for combo in combinations(items, HEADLINES_PER_ROUND):
        if has_clean and not any(h.is_clean for h in combo):
            continue
        score = (
            len({h.reference.technique for h in combo}),
            len({h.source for h in combo if h.source}),
        )
        if score > best_score:
            best_score, best = score, [combo]
        elif score == best_score and best:
            best.append(combo)
    if not has_clean and best_score[0] < HEADLINES_PER_ROUND:
        return 0, []
    return best_score[0], best


def build_round(pool: Iterable[Headline], rng_seed: int | str) -> Round:
    """Pick one event and the three of its headlines with the most distinct techniques.

    Events are compared by the diversity of their best subset; among the best events and
    among equally good subsets (tie-break: more distinct outlets) the seed decides. A round
    with diversity 1 is returned only when no event in the pool can do better.

    Raises ValueError if no event in the pool can form a round.
    """
    events = _group_by_event(pool)
    scored = {
        event_id: _best_subsets(items) for event_id, items in sorted(events.items())
    }
    scored = {event_id: s for event_id, s in scored.items() if s[1]}
    if not scored:
        raise ValueError(
            "no event in the pool can form a round: it needs three undisputed headlines "
            "including a no_technique one, or three distinct techniques"
        )

    top = max(diversity for diversity, _ in scored.values())
    rng = random.Random(rng_seed)
    event_id = rng.choice(sorted(e for e, (d, _) in scored.items() if d == top))
    tied = scored[event_id][1]
    event_rng = random.Random(f"{rng_seed}|{event_id}")
    chosen = list(event_rng.choice(tied))
    event_rng.shuffle(chosen)  # the clean headline must not always sit in the same slot
    return Round(event_id=event_id, headlines=(chosen[0], chosen[1], chosen[2]))
