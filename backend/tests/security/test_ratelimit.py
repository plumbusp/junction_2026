"""Per-key sliding-window rate limiter."""

from __future__ import annotations

import pytest
from app.security.ratelimit import (
    DEFAULT_ANALYZE_LIMIT,
    RateLimiter,
    analyze_limit_from_env,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_limit_blocks_then_recovers_after_the_window() -> None:
    clock = FakeClock()
    limiter = RateLimiter(limit=3, window=60, clock=clock)

    assert [limiter.hit("1.2.3.4")[0] for _ in range(3)] == [True, True, True]
    allowed, retry_after = limiter.hit("1.2.3.4")
    assert allowed is False and 1 <= retry_after <= 60
    assert limiter.hit("5.6.7.8")[0] is True  # other clients are not affected

    clock.now += 61
    assert limiter.hit("1.2.3.4")[0] is True


def test_limit_configuration() -> None:
    assert analyze_limit_from_env({}) == DEFAULT_ANALYZE_LIMIT == 20
    assert analyze_limit_from_env({"ANALYZE_RATE_LIMIT_PER_MINUTE": "5"}) == 5
    assert analyze_limit_from_env({"ANALYZE_RATE_LIMIT_PER_MINUTE": "zero"}) == 20
    with pytest.raises(ValueError):
        RateLimiter(limit=0)
