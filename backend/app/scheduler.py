"""In-process scheduler for the daily challenge job.

An asyncio task started in the FastAPI lifespan: sleep until the next HH:MM in
``DAILY_TZ``, run the job in a worker thread, repeat. No dependency beyond the standard
library (``zoneinfo``). The job itself is idempotent per local date and recorded in the
``job_runs`` table (``app.news.daily``), so restarts never run a day twice.

On startup, if today's slot has already passed, a catch-up run starts in the background
(it never blocks startup). Failures are logged by error class only and retried at most
once per hour, up to three attempts per date; they never propagate to the app.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, time, timedelta, timezone, tzinfo
from functools import lru_cache
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import Settings

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_SECONDS = 3600
BUSY_RECHECK_SECONDS = 300
MAX_SLEEP_SECONDS = 3600  # sleep in chunks so a clock change is noticed within an hour


class Outcome(Protocol):
    """What the job runner reports (``app.news.daily.RunOutcome`` fits)."""

    status: str
    attempts: int
    retry_at: datetime | None


Runner = Callable[[date], Outcome]
Clock = Callable[[], datetime]
Sleep = Callable[[float], Awaitable[None]]


def utc_now() -> datetime:
    return datetime.now(UTC)


@lru_cache(maxsize=8)
def resolve_tz(name: str, fallback_offset_minutes: int = 120) -> tzinfo:
    """The IANA zone ``name``, or a fixed UTC offset when the tz database is missing.

    Slim container images may ship without tz data. The fixed offset ignores daylight
    saving time, so the job can drift by one hour in summer; the warning says so.
    """
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        log.warning(
            "time zone data for %r not found; using fixed UTC%+d min (no daylight saving)",
            name,
            fallback_offset_minutes,
        )
        return timezone(timedelta(minutes=fallback_offset_minutes))


def settings_tz(settings: Settings) -> tzinfo:
    return resolve_tz(settings.daily_tz, settings.daily_tz_fallback_offset_minutes)


def local_today(settings: Settings, now: datetime | None = None) -> date:
    """Today's date in the configured zone."""
    return (now or utc_now()).astimezone(settings_tz(settings)).date()


def slot_for(day: date, hour: int, minute: int, tz: tzinfo) -> datetime:
    """The run time on local ``day``: HH:MM in ``tz``, as an aware datetime.

    DST edges: a time that does not exist (spring-forward gap) runs right after the gap
    (03:30 becomes 04:30 when 03:00 jumps to 04:00); an ambiguous time (fall-back
    overlap) uses its first occurrence. Either way a date has exactly one slot.
    """
    naive = datetime.combine(day, time(hour, minute))
    local = naive.replace(tzinfo=tz)
    shifted = local.astimezone(UTC).astimezone(tz)
    if shifted.replace(tzinfo=None) != naive:
        return shifted
    return local


def next_slot(now: datetime, hour: int, minute: int, tz: tzinfo) -> datetime:
    """The first slot strictly after ``now``."""
    today = now.astimezone(tz).date()
    for offset in (0, 1, 2):
        slot = slot_for(today + timedelta(days=offset), hour, minute, tz)
        if slot > now:
            return slot
    raise RuntimeError("no schedule slot found")  # pragma: no cover


class Scheduler:
    """Runs ``runner(local_date)`` daily at the configured time. Created in the lifespan."""

    def __init__(
        self,
        settings: Settings,
        runner: Runner,
        *,
        clock: Clock = utc_now,
        sleep: Sleep = asyncio.sleep,
        max_attempts: int = MAX_ATTEMPTS,
        retry_seconds: float = RETRY_SECONDS,
    ) -> None:
        self.hour = settings.daily_job_hour
        self.minute = settings.daily_job_minute
        self.tz = settings_tz(settings)
        self._runner = runner
        self._clock = clock
        self._sleep = sleep
        self._max_attempts = max_attempts
        self._retry_seconds = retry_seconds
        self._task: asyncio.Task | None = None

    # -- lifecycle --

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._main(), name="daily-scheduler")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    # -- behaviour --

    async def _main(self) -> None:
        try:
            await self.catch_up()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - never let the scheduler take the app down
            log.error("daily catch-up crashed: %s", type(exc).__name__)
        while True:
            try:
                slot = next_slot(self._clock(), self.hour, self.minute, self.tz)
                await self._sleep_until(slot)
                await self.run_date(slot.date())
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.error("daily scheduler iteration failed: %s", type(exc).__name__)
                await self._sleep(60)

    async def catch_up(self) -> None:
        """Run today's job now if its slot has passed (the job skips a day already done)."""
        now = self._clock()
        today = now.astimezone(self.tz).date()
        if slot_for(today, self.hour, self.minute, self.tz) <= now:
            await self.run_date(today)

    async def _sleep_until(self, moment: datetime) -> None:
        while (remaining := (moment - self._clock()).total_seconds()) > 0:
            await self._sleep(min(remaining, MAX_SLEEP_SECONDS))

    async def run_date(self, day: date) -> None:
        """Run the job for ``day``, retrying a failure hourly up to ``max_attempts``."""
        for _ in range(self._max_attempts + 2):
            try:
                outcome = await asyncio.to_thread(self._runner, day)
            except Exception as exc:  # noqa: BLE001
                log.error("daily job crashed: %s", type(exc).__name__)
                await self._sleep(self._retry_seconds)
                continue
            if outcome.status in ("ok", "done", "exhausted"):
                return
            if outcome.status in ("running", "busy"):
                await self._sleep(BUSY_RECHECK_SECONDS)
            elif outcome.status == "retry_later" and outcome.retry_at is not None:
                await self._sleep_until(outcome.retry_at)
            elif outcome.attempts >= self._max_attempts:
                log.error("daily job for %s failed; giving up", day.isoformat())
                return
            else:
                await self._sleep(self._retry_seconds)
