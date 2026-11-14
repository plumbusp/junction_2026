"""Daily run wrapper: per-date idempotency, failure handling, lock, and the CLI."""

from __future__ import annotations

import importlib.util
import logging
from datetime import UTC, date, datetime, timedelta
from functools import partial
from pathlib import Path

import pytest
from app import db
from app.config import REPO_ROOT, Settings
from app.news import daily, job

T0 = datetime(2026, 11, 14, 0, 0, tzinfo=UTC)
TODAY = date(2026, 11, 14)


class FakePipeline:
    def __init__(self, *, fail_with: Exception | None = None) -> None:
        self.dates: list[str] = []
        self.fail_with = fail_with

    def __call__(self, db_path, settings, *, local_date, now, **kwargs):
        self.dates.append(local_date)
        if self.fail_with is not None:
            raise self.fail_with
        return job.LiveRoundsResult(n_items=10, n_labelled=4, n_ready=1)


def test_second_run_for_the_same_date_is_a_no_op_and_a_new_date_runs(
    tmp_db_path: Path,
) -> None:
    pipeline = FakePipeline()
    run = partial(
        daily.run_daily_job, Settings(), tmp_db_path, pipeline=pipeline, now=T0
    )

    first = run(TODAY)
    second = run(TODAY)
    next_day = run(TODAY + timedelta(days=1))

    assert (first.status, second.status, next_day.status) == ("ok", "done", "ok")
    assert pipeline.dates == ["2026-11-14", "2026-11-15"]
    assert db.get_job_run(tmp_db_path, "2026-11-14")["counts"]["labelled"] == 4
    assert run(TODAY, force=True).status == "ok"
    assert len(pipeline.dates) == 3


def test_failure_is_recorded_by_class_only_and_retried_later(
    tmp_db_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    pipeline = FakePipeline(
        fail_with=RuntimeError("key sk-secret-123 leaked in message")
    )
    run = partial(daily.run_daily_job, Settings(), tmp_db_path, pipeline=pipeline)

    failed = run(TODAY, now=T0)
    soon = run(TODAY, now=T0 + timedelta(minutes=5))
    later = run(TODAY, now=T0 + timedelta(hours=1, minutes=1))

    assert (failed.status, failed.error) == ("failed", "RuntimeError")
    assert soon.status == "retry_later" and soon.retry_at == T0 + timedelta(hours=1)
    assert later.status == "failed" and later.attempts == 2
    assert len(pipeline.dates) == 2
    row = db.get_job_run(tmp_db_path, "2026-11-14")
    assert row["status"] == "failed" and row["error"] == "RuntimeError"
    assert "sk-secret" not in caplog.text and "sk-secret" not in str(row)


def test_a_run_inside_the_process_is_refused_while_another_holds_the_lock(
    tmp_db_path: Path,
) -> None:
    pipeline = FakePipeline()
    assert daily._RUN_LOCK.acquire(blocking=False)
    try:
        outcome = daily.run_daily_job(
            Settings(), tmp_db_path, TODAY, pipeline=pipeline, now=T0
        )
    finally:
        daily._RUN_LOCK.release()

    assert outcome.status == "busy" and pipeline.dates == []


def test_rebuild_today_reruns_a_finished_day_but_not_next_to_a_running_claim(
    tmp_db_path: Path,
) -> None:
    seen: list[dict] = []

    def pipeline(db_path, settings, *, local_date, now, **kwargs):
        seen.append(kwargs)
        return job.LiveRoundsResult(n_labelled=2, n_ready=1)

    run = partial(
        daily.run_daily_job, Settings(), tmp_db_path, pipeline=pipeline, now=T0
    )
    assert run(TODAY).status == "ok"
    assert run(TODAY).status == "done"

    rebuilt = run(TODAY, rebuild=True)

    assert rebuilt.status == "ok" and seen[-1] == {"rebuild": True}
    assert len(seen) == 2
    assert db.get_job_run(tmp_db_path, "2026-11-14")["attempts"] >= 1
    # Another live run holds the claim: the rebuild refuses before anything is deleted.
    other = TODAY + timedelta(days=1)
    db.claim_job_run(tmp_db_path, other.isoformat(), now=T0)
    refused = run(other, rebuild=True)
    assert refused.status == "running" and len(seen) == 2


def _load_run_daily_script():
    spec = importlib.util.spec_from_file_location(
        "run_daily_seed", REPO_ROOT / "scripts" / "news" / "run_daily.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_live_seed_prints_counts_only_and_respects_idempotency(
    tmp_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pipeline = FakePipeline()
    monkeypatch.setattr(daily, "get_settings", lambda: Settings(db_path=tmp_db_path))
    monkeypatch.setattr(
        daily, "run_daily_job", partial(daily.run_daily_job, pipeline=pipeline)
    )
    script = _load_run_daily_script()

    assert script.main(["--live", "--seed"]) == 0
    first = capsys.readouterr().out
    assert script.main(["--live", "--seed"]) == 0
    second = capsys.readouterr().out
    assert script.main(["--live", "--seed", "--force"]) == 0

    assert "status: ok" in first and "labelled: 4" in first
    assert "status: done" in second and "--force" in second
    assert len(pipeline.dates) == 2
    assert script.main(["--live", "--seed", "--rebuild-today"]) == 0
    assert "status: ok" in capsys.readouterr().out and len(pipeline.dates) == 3


def test_cli_prints_a_note_when_the_daily_has_no_technique(
    tmp_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def pipeline(db_path, settings, *, local_date, now, **kwargs):
        return job.LiveRoundsResult(
            daily_round_id="r-x", daily_diversity=1, daily_has_technique=False
        )

    monkeypatch.setattr(daily, "get_settings", lambda: Settings(db_path=tmp_db_path))
    monkeypatch.setattr(
        daily, "run_daily_job", partial(daily.run_daily_job, pipeline=pipeline)
    )

    assert daily.run_cli(tmp_db_path) == 0

    out = capsys.readouterr().out
    assert "daily_has_technique: 0" in out
    assert "no reference technique" in out


def test_cli_seed_needs_live_and_excludes_dry_run() -> None:
    script = _load_run_daily_script()

    for argv in (["--seed"], ["--dry-run", "--live", "--seed"], ["--rebuild-today"]):
        with pytest.raises(SystemExit):
            script.main(argv)
