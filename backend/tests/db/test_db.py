"""app.db: candidate rounds, disputed-reference filtering, answers."""

from __future__ import annotations

from pathlib import Path

import pytest
from app import db

from tests.db.rounds_fixture import (
    CLEAN,
    DISPUTED,
    FEAR,
    LOADED,
    seed_worked_example,
    stub_verdict,
    worked_example_items,
)


def test_insert_candidate_round_excludes_disputed_and_keeps_a_clean_headline(
    tmp_db_path: Path,
) -> None:
    result = seed_worked_example(tmp_db_path)

    assert (result.status, result.created, result.n_items, result.n_in_play) == (
        db.STATUS_READY,
        True,
        4,
        3,
    )
    stored = db.get_round(tmp_db_path, result.round_id)
    assert stored is not None and stored.core_mode == "stub"
    texts = {item.text for item in stored.items}
    assert texts == {LOADED, FEAR, CLEAN}
    assert DISPUTED not in texts
    assert any(item.reference.technique == "no_technique" for item in stored.items)
    assert db.list_ready_round_ids(tmp_db_path, "stub") == [result.round_id]
    assert db.list_ready_round_ids(tmp_db_path, "live") == []


def test_reinserting_a_cluster_is_a_no_op(tmp_db_path: Path) -> None:
    first = seed_worked_example(tmp_db_path)
    second = seed_worked_example(tmp_db_path)

    assert second.created is False
    assert second.round_id == first.round_id
    assert db.list_ready_round_ids(tmp_db_path, "stub") == [first.round_id]


def test_event_without_clean_headline_is_stored_as_ineligible(
    tmp_db_path: Path,
) -> None:
    items, _ = worked_example_items()
    refs = [stub_verdict("loaded_language", ["slams"])] * 3 + [
        stub_verdict("no_technique", [], disputed=True)
    ]

    result = db.insert_candidate_round(
        tmp_db_path, event="No clean headline", items=items, references=refs
    )

    assert result.status == db.STATUS_INELIGIBLE
    assert result.n_in_play == 0
    assert db.list_ready_round_ids(tmp_db_path, "stub") == []


def test_live_verdicts_mark_the_round_live(tmp_db_path: Path) -> None:
    items, _ = worked_example_items()
    models = ("claude", "gpt", "deepseek")
    refs = [
        stub_verdict("loaded_language", ["slams"], models=models),
        stub_verdict("appeal_to_fear", ["drown"], models=models),
        stub_verdict("no_technique", [], models=models),
        stub_verdict("doubt", ["hiding"], models=models),
    ]

    result = db.insert_candidate_round(
        tmp_db_path, event="Fares", items=items, references=refs
    )

    assert db.list_ready_round_ids(tmp_db_path, "live") == [result.round_id]


def test_insert_candidate_round_rejects_mismatched_lengths(tmp_db_path: Path) -> None:
    items, refs = worked_example_items()
    with pytest.raises(ValueError, match="same length"):
        db.insert_candidate_round(
            tmp_db_path, event="x", items=items, references=refs[:2]
        )


def test_second_answer_for_the_same_item_is_refused(tmp_db_path: Path) -> None:
    result = seed_worked_example(tmp_db_path)
    item = db.get_round(tmp_db_path, result.round_id).items[0]
    kwargs = {
        "player_id": "player-1234567890abcd",
        "round_id": result.round_id,
        "item_id": item.item_id,
        "technique": "doubt",
        "selected_words": [],
        "technique_points": 0,
        "evidence_points": 0,
        "total": 0,
    }
    db.insert_answer(tmp_db_path, **kwargs)
    with pytest.raises(db.DuplicateAnswerError):
        db.insert_answer(tmp_db_path, **kwargs)
    assert db.answer_totals(tmp_db_path, kwargs["player_id"], result.round_id) == [0]


def test_player_round_items_reveal_the_reference_only_for_answered_items(
    tmp_db_path: Path,
) -> None:
    result = seed_worked_example(tmp_db_path)
    items = db.get_round(tmp_db_path, result.round_id).items
    me, other = "player-me-1234567890ab", "player-other-1234567890"

    def answer(player: str, index: int, technique: str, total: int) -> None:
        db.insert_answer(
            tmp_db_path,
            player_id=player,
            round_id=result.round_id,
            item_id=items[index].item_id,
            technique=technique,
            selected_words=[],
            technique_points=0,
            evidence_points=total,
            total=total,
        )

    answer(me, 1, "doubt", 40)
    answer(other, 0, "doubt", 7)  # another player's answers never show up for me

    rows = db.player_round_items(tmp_db_path, me, result.round_id)

    assert [r.item_id for r in rows] == [i.item_id for i in items]  # position order
    assert [r.reference_technique for r in rows] == [
        None,
        items[1].reference.technique,
        None,
    ]
    assert [(r.player_technique, r.total) for r in rows] == [
        (None, None),
        ("doubt", 40),
        (None, None),
    ]
    assert db.recently_completed_round_ids(tmp_db_path, me) == []
    answer(me, 0, "doubt", 0)
    answer(me, 2, "doubt", 0)
    assert db.recently_completed_round_ids(tmp_db_path, me) == [result.round_id]
    assert db.recently_completed_round_ids(tmp_db_path, other) == []
