"""Dispute state machine: queued -> in_review -> rescored | rejected.

Pure transitions on an immutable `Dispute`. Every function returns a new value and
raises `IllegalTransitionError` for a move the current state does not allow.

- One vote changes nothing: a dispute enters `in_review` only once `threshold`
  independent players (distinct voter ids) have disputed the same item, or when the
  team escalates it explicitly (docs/PLAN.md stage 7, "or by team decision").
- A rescored item is flagged, and its new label goes to the dev set only, never eval.
  `rescore` returns the target set name; the caller writes the label and must pass the
  name through `check_target_set` before writing.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from app.core.types import TECHNIQUES

DEFAULT_THRESHOLD = 3
DEV_SET = "dev"


class DisputeStatus(StrEnum):
    QUEUED = "queued"
    IN_REVIEW = "in_review"
    RESCORED = "rescored"
    REJECTED = "rejected"


class IllegalTransitionError(ValueError):
    """Raised when a transition is not allowed from the dispute's current status."""


@dataclass(frozen=True)
class Dispute:
    item_id: str
    status: DisputeStatus
    voters: frozenset[str]
    threshold: int = DEFAULT_THRESHOLD
    flagged: bool = False  # True once the item has been rescored
    new_technique: str | None = None
    new_evidence_spans: tuple[str, ...] = ()


def _require(dispute: Dispute, expected: DisputeStatus, action: str) -> None:
    if dispute.status is not expected:
        raise IllegalTransitionError(
            f"cannot {action} dispute on {dispute.item_id!r}: status is {dispute.status}, "
            f"expected {expected}"
        )


def _check_voter(voter_id: str) -> None:
    if not voter_id:
        raise ValueError("voter_id must be a non-empty string")


def open_dispute(
    item_id: str, voter_id: str, threshold: int = DEFAULT_THRESHOLD
) -> Dispute:
    """Create a queued dispute holding the first vote."""
    if not item_id:
        raise ValueError("item_id must be a non-empty string")
    _check_voter(voter_id)
    if threshold < 2:
        raise ValueError("threshold must be at least 2: one vote changes nothing")
    return Dispute(
        item_id=item_id,
        status=DisputeStatus.QUEUED,
        voters=frozenset({voter_id}),
        threshold=threshold,
    )


def add_vote(dispute: Dispute, voter_id: str) -> Dispute:
    """Record another player's dispute; moves to `in_review` when `threshold` is reached.

    A repeated vote from the same voter is not independent and changes nothing.
    """
    _require(dispute, DisputeStatus.QUEUED, "vote on")
    _check_voter(voter_id)
    if voter_id in dispute.voters:
        return dispute
    voters = dispute.voters | {voter_id}
    status = (
        DisputeStatus.IN_REVIEW
        if len(voters) >= dispute.threshold
        else DisputeStatus.QUEUED
    )
    return replace(dispute, voters=voters, status=status)


def escalate_by_team(dispute: Dispute) -> Dispute:
    """Team decision: move a queued dispute to `in_review` before the threshold is reached."""
    _require(dispute, DisputeStatus.QUEUED, "escalate")
    return replace(dispute, status=DisputeStatus.IN_REVIEW)


def rescore(
    dispute: Dispute,
    new_technique: str,
    new_evidence_spans: list[str] | tuple[str, ...],
) -> tuple[Dispute, str]:
    """Accept the dispute with a new reference label. Returns (dispute, target set name).

    The target set is always "dev": disputed items never enter the eval set.
    """
    _require(dispute, DisputeStatus.IN_REVIEW, "rescore")
    if new_technique not in TECHNIQUES:
        raise ValueError(
            f"unknown technique {new_technique!r}; expected one of {TECHNIQUES}"
        )
    rescored = replace(
        dispute,
        status=DisputeStatus.RESCORED,
        flagged=True,
        new_technique=new_technique,
        new_evidence_spans=tuple(new_evidence_spans),
    )
    return rescored, DEV_SET


def reject(dispute: Dispute) -> Dispute:
    """Close a dispute under review without changing the reference label."""
    _require(dispute, DisputeStatus.IN_REVIEW, "reject")
    return replace(dispute, status=DisputeStatus.REJECTED)


def check_target_set(name: str) -> str:
    """Guard for callers: a disputed label may only be written to the dev set."""
    if name != DEV_SET:
        raise ValueError(f"disputed items may only go to {DEV_SET!r}, never {name!r}")
    return name
