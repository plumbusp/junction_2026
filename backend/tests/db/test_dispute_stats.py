"""db.dispute_stats and the review queries: counts only, reference read from storage."""

from __future__ import annotations

from pathlib import Path

from app import db

from tests.db.disputes_fixture import COMMENT, answer, dispute, items_of


def test_empty_database_has_zero_counts_and_no_rate(tmp_db_path: Path) -> None:
    stats = db.dispute_stats(tmp_db_path, "live")

    assert stats["answers"] == {"n": 0}
    assert stats["disputes"] == {"items": 0, "votes": 0}
    assert stats["dispute_rate"] == {"k": 0, "n": 0, "value": None}
    assert stats["by_status"] == {
        "n": 0, "queued": 0, "in_review": 0, "rescored": 0, "rejected": 0,
    }
    assert stats["by_reference"] == stats["pairs"] == []


def test_counts_pairs_and_rates(tmp_db_path: Path) -> None:
    loaded, fear, clean = _by_reference(items_of(tmp_db_path, "c1"))
    for player in ("p1", "p2", "p3", "p4"):
        answer(tmp_db_path, loaded, player, "loaded_language")
    answer(tmp_db_path, fear, "p1", "doubt")
    dispute(tmp_db_path, loaded, ["doubt", "doubt", "appeal_to_fear"], status="in_review")
    dispute(tmp_db_path, fear, ["doubt"])
    dispute(tmp_db_path, clean, ["loaded_language"], status="rejected")

    stats = db.dispute_stats(tmp_db_path, "live")

    assert stats["answers"] == {"n": 5}
    assert stats["disputes"] == {"items": 3, "votes": 5}
    assert stats["dispute_rate"] == {"k": 5, "n": 5, "value": 1.0}
    assert stats["by_status"] == {
        "n": 3, "queued": 1, "in_review": 1, "rescored": 0, "rejected": 1,
    }
    assert stats["pairs"][0] == {
        "reference": "loaded_language", "player": "doubt", "votes": 2, "items": 1,
    }
    by_ref = {r["technique"]: r for r in stats["by_reference"]}
    # The client-supplied reference_technique in the vote is ignored: the stored one wins.
    assert set(by_ref) == {"loaded_language", "appeal_to_fear", "no_technique"}
    assert by_ref["loaded_language"] == {
        "technique": "loaded_language", "votes": 3, "answers": 4,
    }
    assert by_ref["no_technique"]["answers"] == 0
    assert {p["technique"]: p["votes"] for p in stats["by_player_technique"]} == {
        "doubt": 3, "appeal_to_fear": 1, "loaded_language": 1,
    }


def test_stats_hold_no_text_or_ids_and_split_by_core_mode(tmp_db_path: Path) -> None:
    live = items_of(tmp_db_path, "live-round")
    stub = items_of(tmp_db_path, "stub-round", live=False)
    dispute(tmp_db_path, live[0], ["doubt"], dispute_id="d-secret-id")
    dispute(tmp_db_path, stub[0], ["doubt"])

    stats = db.dispute_stats(tmp_db_path, "live")

    assert stats["disputes"]["votes"] == 1
    assert db.dispute_stats(tmp_db_path, "stub")["disputes"]["votes"] == 1
    dumped = repr(stats)
    assert COMMENT not in dumped and "d-secret-id" not in dumped and "voter-" not in dumped


def test_set_dispute_status_is_compare_and_set(tmp_db_path: Path) -> None:
    item = items_of(tmp_db_path, "cas")[0]
    dispute_id = dispute(tmp_db_path, item, ["doubt"])

    assert not db.set_dispute_status(
        tmp_db_path, dispute_id, expected="in_review", status="rejected"
    )
    assert db.set_dispute_status(
        tmp_db_path, dispute_id, expected="queued", status="in_review"
    )
    assert db.get_dispute_by_id(tmp_db_path, dispute_id).status == "in_review"
    assert db.get_dispute_by_id(tmp_db_path, "missing") is None


def _by_reference(items: list[db.RoundItem]) -> tuple[db.RoundItem, ...]:
    ordered = {i.reference.technique: i for i in items}
    return ordered["loaded_language"], ordered["appeal_to_fear"], ordered["no_technique"]
