"""Idempotent daily run: claim the local date, run the pipeline, record the outcome.

Used by the in-process scheduler (``app.scheduler``) and by the CLI, which also works in
the production container where ``scripts/`` is not shipped:

    docker exec spotthespin-app-1 python -m app.news.daily [--force]

The ``job_runs`` row makes a date run at most once, across restarts and across processes
(the CLI next to the server). Output and logs carry counts and error class names only:
never headline text, keys or exception messages.
"""

from __future__ import annotations

import argparse
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from app import db
from app.config import Settings, get_settings
from app.news import job
from app.scheduler import local_today, settings_tz

log = logging.getLogger(__name__)

# One uvicorn worker is assumed (SQLite, one process): this lock only stops two runs inside
# that process. Between processes (the CLI next to the server) the job_runs row guards.
_RUN_LOCK = threading.Lock()

Pipeline = Callable[..., job.LiveRoundsResult]


@dataclass(frozen=True)
class RunOutcome:
    # "ok" or "failed" (this call ran), else why it did not run: "done" (already ok),
    # "running" (another process), "retry_later", "exhausted", "busy" (lock held here).
    status: str
    attempts: int = 0
    retry_at: datetime | None = None
    counts: dict[str, int] = field(default_factory=dict)
    failed_outlets: list[str] = field(default_factory=list)
    daily_round_id: str | None = None
    error: str = ""


def run_daily_job(
    settings: Settings,
    db_path: Path | str,
    local_date: date,
    *,
    force: bool = False,
    now: datetime | None = None,
    pipeline: Pipeline = job.run_live_rounds,
    **pipeline_kwargs,
) -> RunOutcome:
    """Run the pipeline for ``local_date`` unless the job_runs row says it must not run."""
    if not _RUN_LOCK.acquire(blocking=False):
        return RunOutcome("busy")
    try:
        started = now or datetime.now(UTC)
        key = local_date.isoformat()
        claim = db.claim_job_run(db_path, key, now=started, force=force)
        if not claim.claimed:
            return RunOutcome(claim.reason, claim.attempts, claim.retry_at)
        try:
            result = pipeline(
                db_path, settings, local_date=key, now=started, **pipeline_kwargs
            )
        except Exception as exc:  # noqa: BLE001 - any failure is recorded and retried
            error = type(exc).__name__  # the class only: messages may carry data
            log.error("daily job for %s failed: %s", key, error)
            db.finish_job_run(
                db_path,
                key,
                status=db.JOB_FAILED,
                now=now or datetime.now(UTC),
                error=error,
            )
            return RunOutcome("failed", claim.attempts, error=error)
        counts = result.counts()
        db.finish_job_run(
            db_path, key, status=db.JOB_OK, now=now or datetime.now(UTC), counts=counts
        )
        log.info("daily job for %s finished: %s", key, counts)
        return RunOutcome(
            "ok",
            claim.attempts,
            counts=counts,
            failed_outlets=result.failed_outlets,
            daily_round_id=result.daily_round_id,
        )
    finally:
        _RUN_LOCK.release()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.news.daily",
        description="Run the daily challenge pipeline once for today (local date in DAILY_TZ).",
    )
    parser.add_argument(
        "--force", action="store_true", help="run even if today's run already succeeded"
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="SQLite path (default: DB_PATH setting)",
    )
    return parser


def run_cli(db_path: Path | None = None, *, force: bool = False) -> int:
    """Run once for today and print counts only. Exit code 1 when the run failed."""
    logging.basicConfig(
        level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s"
    )
    settings = get_settings()
    path = db_path or settings.db_path
    today = local_today(settings)
    outcome = run_daily_job(settings, path, today, force=force)

    print(f"core mode: {settings.core_mode}")
    print(f"local date: {today.isoformat()} ({settings.daily_tz})")
    print(f"timezone in use: {settings_tz(settings)}")
    print(f"status: {outcome.status}")
    if outcome.status in ("done", "running", "retry_later", "exhausted", "busy"):
        hint = " (use --force to run again)" if outcome.status == "done" else ""
        print(f"nothing run: {outcome.status}{hint}")
    if outcome.error:
        print(f"error class: {outcome.error}")
    for name, value in outcome.counts.items():
        print(f"{name}: {value}")
    if outcome.failed_outlets:
        print(f"failed feeds: {', '.join(outcome.failed_outlets)}")
    if outcome.status == "ok":
        print(
            f"daily challenge: {outcome.daily_round_id or 'none (no eligible round)'}"
        )
    print(f"database: {path}")
    return 1 if outcome.status == "failed" else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_cli(args.db_path, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
