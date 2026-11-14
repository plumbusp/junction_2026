"""Test rounds built from hand-written stub verdicts (no classifier call).

The headlines are the invented worked example from docs/PLAN.md, not real news.
Votes use stub model names, so the stored round is marked ``core_mode = "stub"``.
"""

from __future__ import annotations

from pathlib import Path

from app import db
from app.core.types import ModelVote, Verdict

STUB_MODELS = ("stub-a", "stub-b", "stub-c")

LOADED = "Council slams commuters with fare rip-off as prices jump 20%"
FEAR = "Fare hike will drown the city in gridlock, critics warn"
CLEAN = "City council approves 20% increase in bus and tram fares from January"
DISPUTED = "Is the council hiding the real cost of the fare change?"


def stub_verdict(
    technique: str,
    spans: list[str],
    *,
    disputed: bool = False,
    models: tuple[str, ...] = STUB_MODELS,
) -> Verdict:
    if disputed:
        labels = ["doubt", "loaded_language", "no_technique"]
        votes = [
            ModelVote(m, t, [], "stub") for m, t in zip(models, labels, strict=True)
        ]
        return Verdict("no_technique", [], "The models disagreed.", 1, 3, True, votes)
    votes = [ModelVote(m, technique, list(spans), "stub") for m in models]
    return Verdict(
        technique, list(spans), f"Stub explanation for {technique}.", 3, 3, False, votes
    )


def worked_example_items() -> tuple[list[dict], list[Verdict]]:
    items = [
        {"text": LOADED, "outlet": "Outlet A", "published": "2026-11-13T08:00:00Z"},
        {"text": FEAR, "outlet": "Outlet B", "published": "2026-11-13T09:00:00Z"},
        {"text": CLEAN, "outlet": "Outlet C", "published": "2026-11-13T10:00:00Z"},
        {"text": DISPUTED, "outlet": "Outlet D", "published": "2026-11-13T11:00:00Z"},
    ]
    refs = [
        stub_verdict("loaded_language", ["slams", "rip-off"]),
        stub_verdict("appeal_to_fear", ["drown", "gridlock"]),
        stub_verdict("no_technique", []),
        stub_verdict("no_technique", [], disputed=True),
    ]
    return items, refs


def seed_worked_example(
    db_path: Path, cluster_id: str = "fare-hike"
) -> db.InsertResult:
    items, refs = worked_example_items()
    return db.insert_candidate_round(
        db_path,
        event="City council raises public transport fares",
        items=items,
        references=refs,
        cluster_id=cluster_id,
        source_note="Test round: invented headlines.",
    )
