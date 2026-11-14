"""GET /api/round: three headlines about one event. No reference answers, no votes.

Rounds come only from the database (``app.db``). Only rounds whose references were
produced in the current core mode are served, so stub-labelled rounds never appear in
live mode. With no playable round the endpoint answers 503 ``no_rounds``; headlines are
never invented.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app import db
from app.api.deps import player_id
from app.api.errors import ApiError

router = APIRouter()

STUB_NOTE = (
    "Reference answers in this round come from the stub classifier: placeholders, "
    "not model verdicts."
)


@router.get("/api/round")
def get_round(request: Request, player: str = Depends(player_id)) -> dict:
    state = request.app.state
    round_ids = db.list_ready_round_ids(state.db_path, state.settings.core_mode)
    if not round_ids:
        raise ApiError(
            503,
            "no_rounds",
            "No rounds are available yet. New rounds are added from the daily news job.",
        )
    done = db.completed_round_ids(state.db_path, player)
    open_ids = [rid for rid in round_ids if rid not in done]
    if not open_ids:
        raise ApiError(
            409,
            "all_rounds_played",
            "You have played every available round. New rounds arrive with the daily news job.",
        )

    # Newest first: everyone gets the same round of the day until they finish it.
    stored = db.get_round(state.db_path, open_ids[0])
    if stored is None or not stored.items:  # pragma: no cover - ready rounds have items
        raise ApiError(503, "no_rounds", "No rounds are available yet.")
    note = stored.source_note.strip()
    if stored.core_mode == "stub":
        note = f"{note} {STUB_NOTE}".strip()
    return {
        "round_id": stored.round_id,
        "date": stored.event_date,
        "event": stored.event,
        "source_note": note,
        "items": [
            {"item_id": item.item_id, "text": item.text, "outlet": item.outlet}
            for item in stored.items
        ],
    }
