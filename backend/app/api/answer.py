"""POST /api/answer: score one headline with ``app.game.scoring`` and store the answer.

Body: ``{round_id, item_id, technique, selected_words}``. One answer per item per round
per player (anonymous cookie); a second answer is 409. The response matches
``frontend/mock/answer.json`` ("example_response").
"""

from __future__ import annotations

from collections import Counter
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app import db
from app.api.deps import player_id
from app.api.errors import ApiError
from app.core.types import TECHNIQUES
from app.game.scoring import NO_TECHNIQUE, score_headline, score_round, tokenize

router = APIRouter()

Word = Annotated[str, Field(max_length=100)]


class AnswerIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    round_id: str = Field(min_length=1, max_length=100)
    item_id: str = Field(min_length=1, max_length=100)
    technique: str
    selected_words: list[Word] = Field(default_factory=list, max_length=60)

    @field_validator("technique")
    @classmethod
    def _known_technique(cls, value: str) -> str:
        if value not in TECHNIQUES:
            raise ValueError(f"technique must be one of {', '.join(TECHNIQUES)}")
        return value


def precision_recall(words: list[str], spans: list[str]) -> tuple[float, float]:
    """Token precision and recall of the player's words, for display only.

    Uses the scoring tokenizer and the same multiset overlap as ``token_f1``; the points
    themselves always come from ``score_headline``.
    """
    pred = [tok for word in words for tok in tokenize(word)]
    ref = [tok for span in spans for tok in tokenize(span)]
    if not pred and not ref:
        return 1.0, 1.0
    if not pred or not ref:
        return 0.0, 0.0
    overlap = sum((Counter(pred) & Counter(ref)).values())
    return overlap / len(pred), overlap / len(ref)


@router.post("/api/answer")
def post_answer(
    body: AnswerIn, request: Request, player: str = Depends(player_id)
) -> dict:
    state = request.app.state
    item = db.get_round_item(state.db_path, body.round_id, body.item_id)
    if item is None:
        raise ApiError(404, "not_found", "This headline is not part of that round.")

    reference = item.reference
    score = score_headline(body.technique, body.selected_words, reference)
    try:
        db.insert_answer(
            state.db_path,
            player_id=player,
            round_id=body.round_id,
            item_id=body.item_id,
            technique=body.technique,
            selected_words=body.selected_words,
            technique_points=score.technique_points,
            evidence_points=score.evidence_points,
            total=score.total,
        )
    except db.DuplicateAnswerError:
        raise ApiError(
            409,
            "already_answered",
            "You have already answered this headline in this round.",
        ) from None

    no_words = not any(tokenize(word) for word in body.selected_words)
    bonus = reference.technique == NO_TECHNIQUE and no_words
    precision, recall = precision_recall(body.selected_words, reference.evidence_spans)
    totals = db.answer_totals(state.db_path, player, body.round_id)
    total_items = db.count_in_play(state.db_path, body.round_id)
    round_score = score_round(totals) if len(totals) >= total_items else None

    return {
        "round_id": body.round_id,
        "item_id": body.item_id,
        "player": {"technique": body.technique, "selected_words": body.selected_words},
        "reference": db.verdict_to_dict(reference),
        "score": {
            "technique_points": score.technique_points,
            "evidence_points": score.evidence_points,
            "total": score.total,
            "token_f1": round(score.evidence_f1, 4),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "no_technique_bonus": bonus,
        },
        "round": {
            "answered": len(totals),
            "total_items": total_items,
            "round_score": round_score,
        },
    }
