"""GET /api/round: three headlines about one event. No reference answers, no votes.

Rounds come only from the database (``app.db``). Only rounds whose references were
produced in the current core mode are served, so stub-labelled rounds never appear in
live mode. There is exactly one challenge per local date in ``DAILY_TZ``: the round the
daily job designated for today. The three response shapes:

* open round: ``{round_id, date, event, source_note, items[, daily: true]}``; a player who
  answered only some items gets the same round back (answering an item twice is 409);
* completed: ``{completed: true, daily, date, next_edition_at, round_id, round_score,
  items: [{item_id, text, outlet, player_technique, reference_technique, total}]}``, served
  once the player answered every item of today's daily challenge. They are never given
  another round that day. The reference technique is only revealed for items the player
  already answered (it was shown to them right after answering);
* 503 ``no_rounds`` when nothing is playable. Headlines are never invented.

Without a daily challenge for today (the job failed or has not run yet) the newest
unfinished round is served as a fallback (``daily`` false or absent); when the player has
finished every round, the completed shape is returned for the round they finished last.
A round's stored ``diversity`` and ``techniques`` are internal (daily ranking only) and
are never part of the response.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Request

from app import db
from app.api.deps import player_id
from app.api.errors import ApiError
from app.config import Settings
from app.game.scoring import score_round
from app.scheduler import local_today, next_slot, settings_tz, slot_for

router = APIRouter()

STUB_NOTE = (
    "Reference answers in this round come from the stub classifier: placeholders, "
    "not model verdicts."
)


def next_edition_at(settings: Settings, now: datetime, *, daily_exists: bool) -> str:
    """ISO-8601 time (with the zone offset) of the next scheduled edition.

    The next ``DAILY_JOB_HOUR:DAILY_JOB_MINUTE`` in ``DAILY_TZ`` strictly after ``now``.
    When today's challenge already exists, the edition of today's date is out, so a slot
    that is still ahead today (the challenge was made by hand before the job time) does
    not count: the answer is then tomorrow's slot.
    """
    tz = settings_tz(settings)
    hour, minute = settings.daily_job_hour, settings.daily_job_minute
    slot = next_slot(now, hour, minute, tz)
    if daily_exists:
        today: date = now.astimezone(tz).date()
        slot = max(slot, slot_for(today + timedelta(days=1), hour, minute, tz))
    return slot.replace(microsecond=0).isoformat()


def _completed_body(
    state, player: str, round_id: str, *, daily: bool, today: str, event_date: str
) -> dict:
    rows = db.player_round_items(state.db_path, player, round_id)
    totals = [row.total for row in rows if row.total is not None]
    return {
        "completed": True,
        "daily": daily,
        "date": today if daily else event_date,
        "next_edition_at": next_edition_at(
            state.settings, state.clock(), daily_exists=daily
        ),
        "round_id": round_id,
        "round_score": score_round(totals),
        "items": [
            {
                "item_id": row.item_id,
                "text": row.text,
                "outlet": row.outlet,
                "player_technique": row.player_technique,
                "reference_technique": row.reference_technique,
                "total": row.total,
            }
            for row in rows
        ],
    }


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
    today = local_today(state.settings, state.clock()).isoformat()
    done = db.completed_round_ids(state.db_path, player)

    # Today's daily challenge is in round_ids only when labelled in the current core mode.
    daily_id = db.get_daily_challenge(state.db_path, today)
    if daily_id is not None and daily_id in round_ids:
        if daily_id in done:
            stored = db.get_round(state.db_path, daily_id)
            return _completed_body(
                state, player, daily_id, daily=True, today=today,
                event_date=stored.event_date if stored else today,
            )
        round_id, is_daily = daily_id, True
    else:
        # No challenge today: the newest unfinished round, the same for everyone until
        # they finish it.
        open_ids = [rid for rid in round_ids if rid not in done]
        if not open_ids:
            last = next(
                (rid for rid in db.recently_completed_round_ids(state.db_path, player)
                 if rid in round_ids),
                None,
            )
            stored = db.get_round(state.db_path, last) if last else None
            if stored is None:  # pragma: no cover - done is a subset of round_ids
                raise ApiError(503, "no_rounds", "No rounds are available yet.")
            return _completed_body(
                state, player, stored.round_id, daily=False, today=today,
                event_date=stored.event_date,
            )
        round_id, is_daily = open_ids[0], False

    stored = db.get_round(state.db_path, round_id)
    if stored is None or not stored.items:  # pragma: no cover - ready rounds have items
        raise ApiError(503, "no_rounds", "No rounds are available yet.")
    note = stored.source_note.strip()
    if stored.core_mode == "stub":
        note = f"{note} {STUB_NOTE}".strip()
    body = {
        "round_id": stored.round_id,
        "date": today if is_daily else stored.event_date,
        "event": stored.event,
        "source_note": note,
        "items": [
            {"item_id": item.item_id, "text": item.text, "outlet": item.outlet}
            for item in stored.items
        ],
    }
    if is_daily:
        body["daily"] = True
    return body
