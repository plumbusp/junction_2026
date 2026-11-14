"""POST /api/dispute: queue a player's dispute of a reference answer.

Uses the state machine in ``app.game.disputes``: one vote changes nothing; the dispute
enters review after ``threshold`` distinct voters (the anonymous player cookie). The
reference answer, the metrics and ``data/eval`` are never touched here; a rescored item
may only ever go to the dev set (``check_target_set``).
"""

from __future__ import annotations

import threading
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app import db
from app.api.deps import player_id
from app.api.errors import ApiError
from app.core.types import TECHNIQUES
from app.game.disputes import (
    DEV_SET,
    Dispute,
    DisputeStatus,
    add_vote,
    check_target_set,
    open_dispute,
)

router = APIRouter()

# One process, one worker: a lock keeps the read-modify-write of a dispute atomic.
_dispute_lock = threading.Lock()

Word = Annotated[str, Field(max_length=100)]

POLICY = "Confirmed disputes are added to the dev set only, never to the test set."
MESSAGES = {
    DisputeStatus.QUEUED: (
        "Dispute queued. The reference answer does not change until the headline is "
        f"reviewed. {POLICY}"
    ),
    DisputeStatus.IN_REVIEW: (
        "Enough players disagree: this headline is now waiting for review by the team. "
        f"The reference answer does not change until the review is done. {POLICY}"
    ),
    DisputeStatus.RESCORED: "This headline has already been reviewed and rescored. Your dispute was recorded.",
    DisputeStatus.REJECTED: "This headline has already been reviewed. Your dispute was recorded.",
}
REPEAT_NOTE = "You have already disputed this headline; your first dispute is the one that counts. "


class DisputeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    round_id: str = Field(min_length=1, max_length=100)
    item_id: str = Field(min_length=1, max_length=100)
    reference_technique: str | None = Field(default=None, max_length=50)
    player_technique: str
    player_words: list[Word] = Field(default_factory=list, max_length=60)
    comment: str = Field(default="", max_length=1000)

    @field_validator("player_technique")
    @classmethod
    def _known_technique(cls, value: str) -> str:
        if value not in TECHNIQUES:
            raise ValueError(f"player_technique must be one of {', '.join(TECHNIQUES)}")
        return value


@router.post("/api/dispute")
def post_dispute(
    body: DisputeIn, request: Request, voter: str = Depends(player_id)
) -> dict:
    state = request.app.state
    item = db.get_round_item(state.db_path, body.round_id, body.item_id)
    if item is None:
        raise ApiError(404, "not_found", "This headline is not part of that round.")

    target_set = check_target_set(DEV_SET)
    with _dispute_lock:
        record = db.get_dispute(state.db_path, body.item_id)
        if record is None:
            dispute = open_dispute(body.item_id, voter)
            dispute_id = f"d-{uuid.uuid4().hex[:12]}"
            repeat = False
            voters = dispute.voters
        else:
            dispute = Dispute(
                item_id=record.item_id,
                status=DisputeStatus(record.status),
                voters=record.voters,
                threshold=record.threshold,
            )
            dispute_id = record.dispute_id
            repeat = voter in record.voters
            if dispute.status is DisputeStatus.QUEUED:
                dispute = add_vote(dispute, voter)
            # A dispute already in review or closed keeps its status; the vote is recorded.
            voters = record.voters | {voter}
        db.save_dispute_vote(
            state.db_path,
            dispute_id=dispute_id,
            round_id=body.round_id,
            item_id=body.item_id,
            status=str(dispute.status),
            threshold=dispute.threshold,
            voter_id=voter,
            reference_technique=body.reference_technique,
            player_technique=body.player_technique,
            player_words=body.player_words,
            comment=body.comment.strip(),
        )

    message = MESSAGES[dispute.status]
    if repeat:
        message = REPEAT_NOTE + message
    return {
        "dispute_id": dispute_id,
        "round_id": body.round_id,
        "item_id": body.item_id,
        "status": str(dispute.status),
        "disputes_on_item": len(voters),
        "review_threshold": dispute.threshold,
        "target_set": target_set,
        "message": message,
    }
