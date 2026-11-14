"""Schedule computation (incl. DST), catch-up, failure isolation and lifecycle. Fake clock only."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from app import db
from app.config import Settings
from app.main import create_app
from app.scheduler import (
    Scheduler,
    local_today,
    next_slot,
    resolve_tz,
    slot_for,
)
from fastapi.testclient import TestClient

HELSINKI = ZoneInfo("Europe/Helsinki")


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


# ---------- schedule computation ----------


def test_next_slot_is_the_coming_local_midnight() -> None:
    now = _utc(2026, 11, 14, 10, 0)  # 12:00 in Helsinki (UTC+2)

    slot = next_slot(now, 0, 0, HELSINKI)

    assert slot == datetime(2026, 11, 15, 0, 0, tzinfo=HELSINKI)
    assert slot.astimezone(UTC) == _utc(2026, 11, 14, 22, 0)
    # Exactly at the slot the next one is a day later (strictly after).
    assert next_slot(slot, 0, 0, HELSINKI) == datetime(
        2026, 11, 16, 0, 0, tzinfo=HELSINKI
    )


def test_slot_in_the_spring_forward_gap_runs_right_after_it() -> None:
    # 2027-03-28: 03:00 jumps to 04:00 in Helsinki, so 03:30 does not exist.
    slot = slot_for(date(2027, 3, 28), 3, 30, HELSINKI)

    assert slot.astimezone(UTC) == _utc(2027, 3, 28, 1, 30)
    assert slot.hour == 4 and slot.minute == 30
    # The day before is normal, and the day after the zone offset is already +3.
    assert next_slot(_utc(2027, 3, 27, 12), 3, 30, HELSINKI).astimezone(UTC) == _utc(
        2027, 3, 28, 1, 30
    )
    assert slot_for(date(2027, 3, 29), 3, 30, HELSINKI).astimezone(UTC) == _utc(
        2027, 3, 29, 0, 30
    )


def test_slot_in_the_fall_back_overlap_runs_once_at_the_first_occurrence() -> None:
    # 2027-10-31: 04:00 falls back to 03:00, so 03:30 happens twice (00:30Z and 01:30Z).
    first = slot_for(date(2027, 10, 31), 3, 30, HELSINKI)

    assert first.astimezone(UTC) == _utc(2027, 10, 31, 0, 30)
    # After the first occurrence the next slot is the following day, not the repeat.
    nxt = next_slot(_utc(2027, 10, 31, 0, 45), 3, 30, HELSINKI)
    assert nxt.date() == date(2027, 11, 1)


def test_missing_tz_data_falls_back_to_a_fixed_offset(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tz = resolve_tz("Nowhere/Atlantis", 180)

    assert datetime(2026, 7, 1, tzinfo=UTC).astimezone(tz).hour == 3
    assert "not found" in caplog.text
    settings = Settings(
        daily_tz="Nowhere/Atlantis", daily_tz_fallback_offset_minutes=180
    )
    assert local_today(settings, _utc(2026, 11, 14, 21, 30)) == date(2026, 11, 15)


# ---------- behaviour with a fake clock ----------


@dataclass
class FakeOutcome:
    status: str = "ok"
    attempts: int = 1
    retry_at: datetime | None = None


class Harness:
    """A scheduler wired to a fake clock, a fake sleep and a recording runner."""

    def __init__(self, now: datetime, results: list | None = None, **settings) -> None:
        self.now = now
        self.sleeps: list[float] = []
        self.calls: list[date] = []
        self.results = list(results or [])
        self.settings = Settings(**settings)
        self.scheduler = Scheduler(
            self.settings,
            self.run,
            clock=lambda: self.now,
            sleep=self.sleep,
            retry_seconds=3600,
        )

    def run(self, day: date):
        self.calls.append(day)
        result = self.results.pop(0) if self.results else FakeOutcome()
        if isinstance(result, Exception):
            raise result
        return result

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=seconds)


def test_startup_catch_up_runs_today_once_when_the_slot_has_passed() -> None:
    late = Harness(_utc(2026, 11, 14, 10, 0))  # 12:00 Helsinki, past 00:00
    asyncio.run(late.scheduler.catch_up())
    assert late.calls == [date(2026, 11, 14)]

    early = Harness(
        _utc(2026, 11, 14, 10, 0), daily_job_hour=18
    )  # 18:00 is still ahead
    asyncio.run(early.scheduler.catch_up())
    assert early.calls == []


def test_a_failing_job_never_raises_and_is_retried_hourly_up_to_the_limit() -> None:
    h = Harness(
        _utc(2026, 11, 14, 10, 0),
        results=[
            RuntimeError("boom"),
            FakeOutcome("failed", 2),
            FakeOutcome("failed", 3),
        ],
    )

    asyncio.run(h.scheduler.catch_up())  # must not raise

    assert h.calls == [date(2026, 11, 14)] * 3
    assert h.sleeps == [3600, 3600]  # one hour between attempts, then it gives up


def test_a_day_already_done_or_retry_pending_is_respected() -> None:
    now = _utc(2026, 11, 14, 10, 0)
    done = Harness(now, results=[FakeOutcome("done")])
    asyncio.run(done.scheduler.catch_up())
    assert len(done.calls) == 1 and done.sleeps == []

    wait = Harness(
        now,
        results=[
            FakeOutcome("retry_later", 1, now + timedelta(minutes=20)),
            FakeOutcome(),
        ],
    )
    asyncio.run(wait.scheduler.catch_up())
    assert len(wait.calls) == 2 and sum(wait.sleeps) == 20 * 60


def test_loop_runs_each_day_at_the_slot_then_sleeps_to_the_next() -> None:
    h = Harness(_utc(2026, 11, 14, 22, 0))  # 00:00 Helsinki on the 15th is 22:00Z

    async def scenario() -> None:
        task = asyncio.create_task(h.scheduler._main())
        for _ in range(500):
            await asyncio.sleep(0.01)  # the job runs in a worker thread
            if len(h.calls) >= 2:
                break
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())

    # Catch-up for the 15th (clock is exactly at its slot), then the 16th's slot.
    assert h.calls[:2] == [date(2026, 11, 15), date(2026, 11, 16)]


# ---------- app lifecycle ----------


def test_scheduler_starts_and_stops_with_the_app(tmp_db_path: Path) -> None:
    ran = threading.Event()

    def runner(day: date) -> FakeOutcome:
        ran.set()
        return FakeOutcome()

    settings = Settings(core_mode="stub", db_path=tmp_db_path, daily_job_enabled=True)
    app = create_app(
        settings,
        db_path=tmp_db_path,
        daily_runner=runner,
        clock=lambda: _utc(2026, 11, 14, 10, 0),
    )
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert ran.wait(5), "startup catch-up did not run"
    # Leaving the context ran the lifespan shutdown: the task is gone and nothing hangs.


def test_scheduler_is_off_by_default(tmp_db_path: Path) -> None:
    calls: list[date] = []
    app = create_app(
        Settings(core_mode="stub", db_path=tmp_db_path),
        db_path=tmp_db_path,
        daily_runner=lambda day: calls.append(day),
    )
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200

    assert calls == []
    assert db.get_job_run(tmp_db_path, "2026-11-14") is None
