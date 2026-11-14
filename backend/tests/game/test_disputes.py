"""Dispute state machine: queued -> in_review -> rescored | rejected; dev only, never eval."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from app.game.disputes import (
    Dispute,
    DisputeStatus,
    IllegalTransitionError,
    add_vote,
    check_target_set,
    escalate_by_team,
    open_dispute,
    reject,
    rescore,
)


def _in_review() -> Dispute:
    return add_vote(add_vote(open_dispute("item-1", "p1"), "p2"), "p3")


def test_one_vote_changes_nothing_and_threshold_starts_review() -> None:
    d = open_dispute("item-1", "p1")
    assert d.status is DisputeStatus.QUEUED

    d = add_vote(d, "p1")  # same player again is not independent
    d = add_vote(d, "p2")
    assert d.status is DisputeStatus.QUEUED
    assert len(d.voters) == 2

    d = add_vote(d, "p3")
    assert d.status is DisputeStatus.IN_REVIEW


def test_threshold_is_a_parameter() -> None:
    d = add_vote(open_dispute("item-1", "p1", threshold=2), "p2")

    assert d.status is DisputeStatus.IN_REVIEW
    with pytest.raises(ValueError, match="one vote changes nothing"):
        open_dispute("item-1", "p1", threshold=1)


def test_team_can_escalate_a_queued_dispute() -> None:
    assert (
        escalate_by_team(open_dispute("item-1", "p1")).status is DisputeStatus.IN_REVIEW
    )


def test_rescore_flags_item_and_targets_dev_only() -> None:
    d, target = rescore(_in_review(), "doubt", ["allegedly"])

    assert d.status is DisputeStatus.RESCORED
    assert d.flagged is True
    assert (d.new_technique, d.new_evidence_spans) == ("doubt", ("allegedly",))
    assert target == "dev"
    assert check_target_set(target) == "dev"
    with pytest.raises(ValueError, match="never 'eval'"):
        check_target_set("eval")
    with pytest.raises(ValueError, match="unknown technique"):
        rescore(_in_review(), "propaganda", [])


def test_reject_closes_without_flag() -> None:
    d = reject(_in_review())

    assert d.status is DisputeStatus.REJECTED
    assert d.flagged is False


def _queued() -> Dispute:
    return open_dispute("item-1", "p1")


def _rescored() -> Dispute:
    return rescore(_in_review(), "doubt", [])[0]


def _rejected() -> Dispute:
    return reject(_in_review())


MOVES: dict[str, Callable[[Dispute], object]] = {
    "vote": lambda d: add_vote(d, "p9"),
    "escalate": escalate_by_team,
    "rescore": lambda d: rescore(d, "doubt", []),
    "reject": reject,
}


@pytest.mark.parametrize(
    ("make", "move"),
    [
        (_queued, "rescore"),
        (_queued, "reject"),
        (_in_review, "vote"),
        (_in_review, "escalate"),
        *[(_rescored, m) for m in MOVES],
        *[(_rejected, m) for m in MOVES],
    ],
)
def test_illegal_transitions_raise(make: Callable[[], Dispute], move: str) -> None:
    with pytest.raises(IllegalTransitionError):
        MOVES[move](make())


def test_invalid_ids_raise() -> None:
    with pytest.raises(ValueError, match="item_id"):
        open_dispute("", "p1")
    with pytest.raises(ValueError, match="voter_id"):
        add_vote(_queued(), "")
