"""In-memory sliding-window rate limiter, keyed by client IP.

One process, one worker (see Dockerfile), so in-memory state is enough. The default
for /api/analyze is 20 requests per minute per IP, configurable with the environment
variable ``ANALYZE_RATE_LIMIT_PER_MINUTE``.
"""

from __future__ import annotations

import math
import os
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping

DEFAULT_ANALYZE_LIMIT = 20
WINDOW_SECONDS = 60.0
ENV_VAR = "ANALYZE_RATE_LIMIT_PER_MINUTE"
_PRUNE_AT = 10_000  # number of tracked keys that triggers a cleanup of idle keys


def analyze_limit_from_env(environ: Mapping[str, str] | None = None) -> int:
    """Read the per-minute limit; invalid or non-positive values fall back to the default."""
    raw = (environ if environ is not None else os.environ).get(ENV_VAR, "").strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_ANALYZE_LIMIT
    return value if value > 0 else DEFAULT_ANALYZE_LIMIT


class RateLimiter:
    """Allow at most ``limit`` hits per ``window`` seconds for each key."""

    def __init__(
        self,
        limit: int,
        window: float = WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        self.limit = limit
        self.window = window
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> tuple[bool, int]:
        """Record one request for ``key``. Returns (allowed, retry_after_seconds)."""
        now = self._clock()
        with self._lock:
            if len(self._hits) >= _PRUNE_AT:
                self._prune(now)
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                retry_after = max(1, math.ceil(hits[0] + self.window - now))
                return False, retry_after
            hits.append(now)
            return True, 0

    def _prune(self, now: float) -> None:
        idle = [k for k, h in self._hits.items() if not h or h[-1] <= now - self.window]
        for key in idle:
            del self._hits[key]
