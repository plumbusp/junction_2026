"""Helpers that put disputes and answers straight into a test database (no HTTP)."""

from __future__ import annotations

from pathlib import Path

from app import db

from tests.db.rounds_fixture import LIVE_MODELS, seed_simple_round

COMMENT = "PRIVATE-COMMENT-TEXT sounds like exaggeration to me"


def items_of(db_path: Path, cluster_id: str, *, live: bool = True) -> list[db.RoundItem]:
    """Seed a ready round (live-labelled unless ``live`` is False) and return its items."""
    kwargs = {"models": LIVE_MODELS} if live else {}
    result = seed_simple_round(db_path, cluster_id, **kwargs)
    return db.get_round(db_path, result.round_id).items


def answer(db_path: Path, item: db.RoundItem, player: str, technique: str) -> None:
    db.insert_answer(
        db_path,
        player_id=player,
        round_id=item.round_id,
        item_id=item.item_id,
        technique=technique,
        selected_words=[],
        technique_points=0,
        evidence_points=0,
        total=0,
    )


def dispute(
    db_path: Path,
    item: db.RoundItem,
    picks: list[str],
    *,
    status: str = "queued",
    dispute_id: str | None = None,
) -> str:
    """One dispute on ``item`` with one vote per entry of ``picks`` (the players' techniques)."""
    dispute_id = dispute_id or f"d-{item.item_id}"
    for number, technique in enumerate(picks):
        db.save_dispute_vote(
            db_path,
            dispute_id=dispute_id,
            round_id=item.round_id,
            item_id=item.item_id,
            status=status,
            threshold=3,
            voter_id=f"voter-{item.item_id}-{number}".ljust(16, "x"),
            reference_technique="client-supplied-and-ignored",
            player_technique=technique,
            player_words=[],
            comment=COMMENT,
        )
    return dispute_id
