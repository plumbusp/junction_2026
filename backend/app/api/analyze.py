"""POST /api/analyze: classify pasted text or the headline and lead of an https URL.

Body: ``{"text": ...}`` or ``{"url": ...}`` (exactly one). Response: the Verdict fields
plus ``text`` (the analyzed text), ``source_url`` and ``title`` (URL input only).
``evidence_spans`` are strings, each an exact substring of ``text``; the page computes
offsets itself. Rate-limited per client IP, and capped globally per local day
(``ANALYZE_DAILY_CAP``, 429 ``daily_limit``). A core failure answers 503 with a short
message; the user's text is never logged.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, time, timedelta

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app import db
from app.api.deps import client_ip
from app.api.errors import ApiError
from app.core.classifier import classify
from app.scheduler import local_today, settings_tz
from app.security import ssrf

log = logging.getLogger(__name__)
router = APIRouter()

MAX_TEXT_CHARS = 5000  # matches MAX_TEXT on the Analyze page
URL_TEXT_MAX_CHARS = 2000  # headline and lead of a fetched article


class AnalyzeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str | None = None
    url: str | None = Field(default=None, max_length=ssrf.MAX_URL_LENGTH)

    @model_validator(mode="after")
    def _exactly_one(self) -> AnalyzeIn:
        has_text = self.text is not None and self.text.strip() != ""
        has_url = self.url is not None and self.url.strip() != ""
        if has_text == has_url:
            raise ValueError("send exactly one of 'text' or 'url'")
        return self


def rate_limited(request: Request) -> None:
    limiter = request.app.state.analyze_limiter
    allowed, retry_after = limiter.hit(client_ip(request))
    if not allowed:
        raise ApiError(
            429,
            "rate_limited",
            "Too many requests. Please wait a minute and try again.",
            headers={"Retry-After": str(retry_after)},
        )


def _exact_spans(spans: list, text: str) -> list[str]:
    return [s for s in spans if isinstance(s, str) and s and s in text]


def _seconds_until_local_midnight(settings, now: datetime) -> int:
    tz = settings_tz(settings)
    local = now.astimezone(tz)
    midnight = datetime.combine(local.date() + timedelta(days=1), time(0, 0), tzinfo=tz)
    return max(1, math.ceil((midnight - local).total_seconds()))


DAILY_LIMIT_DETAIL = (
    "Stop the presses: the analysis desk has reached its daily limit and is closed for "
    "the day. The next edition goes to print at midnight ({tz}), so please come back "
    "tomorrow. The game and the track record stay open."
)


@router.post("/api/analyze", dependencies=[Depends(rate_limited)])
def post_analyze(body: AnalyzeIn, request: Request) -> dict:
    """Spend protection: one slot of the daily cap is taken before any work is done and
    given back if the analysis fails, so only successful analyses count. The core does
    not report cache hits, so a cached text counts like any other."""
    state = request.app.state
    now = state.clock()
    today = local_today(state.settings, now).isoformat()
    cap = state.settings.analyze_daily_cap
    if not db.analyze_reserve(state.db_path, today, cap):
        raise ApiError(
            429,
            "daily_limit",
            DAILY_LIMIT_DETAIL.format(tz=state.settings.daily_tz),
            headers={
                "Retry-After": str(_seconds_until_local_midnight(state.settings, now))
            },
        )
    try:
        return _analyze(body, state)
    except BaseException:
        if cap > 0:
            db.analyze_release(state.db_path, today)
        raise


def _analyze(body: AnalyzeIn, state) -> dict:
    source_url: str | None = None
    title: str | None = None
    if body.url is not None and body.url.strip():
        try:
            page = state.fetch_url(body.url.strip())
            extracted = ssrf.extract_text(page, URL_TEXT_MAX_CHARS)
        except ssrf.FetchError as exc:
            raise ApiError(exc.status_code, exc.code, str(exc)) from None
        text = extracted.text
        source_url = page.url
        title = extracted.title
    else:
        text = (body.text or "").strip()
        if len(text) > MAX_TEXT_CHARS:
            raise ApiError(
                413,
                "text_too_long",
                f"The text is longer than {MAX_TEXT_CHARS} characters.",
            )

    try:
        verdict = classify(text)
    except ValueError:
        raise ApiError(
            422, "invalid_text", "Enter a non-empty text to analyze."
        ) from None
    except Exception as exc:  # noqa: BLE001 - any core failure is a 503 for the client
        log.warning("classify failed: %s", type(exc).__name__)
        raise ApiError(
            503,
            "core_unavailable",
            "The classifier is temporarily unavailable. Please try again later.",
        ) from None

    data = db.verdict_to_dict(verdict)
    data["evidence_spans"] = _exact_spans(data["evidence_spans"], text)
    for vote in data["votes"]:
        vote["evidence_spans"] = _exact_spans(vote["evidence_spans"], text)
    return {"text": text, "source_url": source_url, "title": title, **data}
