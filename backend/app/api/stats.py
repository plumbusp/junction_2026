"""GET /api/stats/disputes: where players disagree with the reference answers.

Public and cheap: counts only, never the text of a comment, a player id or a dispute id.
The reference technique is read from the stored reference of each headline (not from the
client's request). Player disputes are opinions, not labels: nothing here changes a
reference answer, the metrics or the frozen test set. The result is cached for a few
seconds in memory, so a burst of page views costs one set of queries.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app import db

router = APIRouter()

DEFAULT_TTL_SECONDS = 5.0
TOP_PAIRS = 10  # pairs returned; the page shows the first few


class StatsCache:
    """One cached value for ``ttl`` seconds (single process, so in-memory is enough)."""

    def __init__(
        self, ttl: float = DEFAULT_TTL_SECONDS, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._value: dict | None = None
        self._stamp = 0.0

    def get(self, compute: Callable[[], dict]) -> dict:
        with self._lock:
            now = self._clock()
            if self._value is None or now - self._stamp >= self.ttl:
                self._value = compute()
                self._stamp = now
            return self._value


@router.get("/api/stats/disputes")
def get_dispute_stats(request: Request) -> JSONResponse:
    state = request.app.state

    def compute() -> dict:
        data = db.dispute_stats(state.db_path, state.settings.core_mode)
        data["pairs"] = data["pairs"][:TOP_PAIRS]
        return data

    data = state.stats_cache.get(compute)
    return JSONResponse(
        data, headers={"Cache-Control": f"public, max-age={int(state.stats_cache.ttl)}"}
    )
