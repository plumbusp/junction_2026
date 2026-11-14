"""Daily challenge storage: schema migration, selection rule and per-date job idempotency."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app import db

from tests.db.rounds_fixture import LIVE_MODELS, seed_simple_round

T0 = datetime(2026, 11, 14, 0, 0, tzinfo=UTC)
DAY = "2026-11-14"


def test_old_database_is_migrated_and_keeps_its_rows(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE rounds (round_id TEXT PRIMARY KEY, cluster_id TEXT, event TEXT NOT NULL,"
        " event_date TEXT NOT NULL, source_note TEXT NOT NULL DEFAULT '',"
        " core_mode TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);"
        "INSERT INTO rounds VALUES ('r-old', 'c', 'Old', '2026-11-01', '', 'stub', 'ready', 'x');"
    )
    conn.commit()
    conn.close()

    db.init_db(path)
    db.init_db(path)  # idempotent

    with db.connect(path) as c:
        row = c.execute("SELECT event, n_outlets FROM rounds").fetchone()
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master")}
    assert (row["event"], row["n_outlets"]) == ("Old", None)
    assert {"daily_challenge", "job_runs"} <= tables


def test_daily_pick_prefers_most_outlets_then_freshest(tmp_db_path: Path) -> None:
    few = seed_simple_round(tmp_db_path, "few", n_outlets=3)
    many = seed_simple_round(tmp_db_path, "many", n_outlets=9)
    older = seed_simple_round(
        tmp_db_path, "older", n_outlets=9, published="2026-11-12T10:00:00Z"
    )
    ids = [few.round_id, many.round_id, older.round_id]

    assert db.pick_daily_challenge(tmp_db_path, DAY, ids, "stub") == many.round_id
    # An existing designation for the date is kept, never replaced.
    assert (
        db.pick_daily_challenge(tmp_db_path, DAY, [few.round_id], "stub")
        == many.round_id
    )
    # Next day: the used round is skipped; the tie on 9 outlets was decided by freshness.
    assert (
        db.pick_daily_challenge(tmp_db_path, "2026-11-15", ids, "stub")
        == older.round_id
    )
    assert db.get_daily_challenge(tmp_db_path, DAY) == many.round_id


def test_daily_pick_skips_wrong_mode_and_ineligible_rounds(tmp_db_path: Path) -> None:
    live = seed_simple_round(tmp_db_path, "live", n_outlets=9, models=LIVE_MODELS)
    stub = seed_simple_round(tmp_db_path, "stub", n_outlets=3)

    assert db.pick_daily_challenge(tmp_db_path, DAY, [live.round_id], "stub") is None
    assert db.pick_daily_challenge(tmp_db_path, DAY, [], "stub") is None
    assert (
        db.pick_daily_challenge(
            tmp_db_path, DAY, [live.round_id, stub.round_id], "stub"
        )
        == stub.round_id
    )


def test_job_runs_once_per_date_and_new_date_runs(tmp_db_path: Path) -> None:
    first = db.claim_job_run(tmp_db_path, DAY, now=T0)
    assert (first.claimed, first.attempts) == (True, 1)
    assert db.claim_job_run(tmp_db_path, DAY, now=T0).reason == "running"

    db.finish_job_run(tmp_db_path, DAY, status=db.JOB_OK, now=T0, counts={"ready": 2})
    again = db.claim_job_run(tmp_db_path, DAY, now=T0 + timedelta(days=0, hours=5))
    assert (again.claimed, again.reason) == (False, "done")
    assert db.get_job_run(tmp_db_path, DAY)["counts"] == {"ready": 2}

    assert db.claim_job_run(tmp_db_path, "2026-11-15", now=T0).claimed
    assert db.claim_job_run(tmp_db_path, DAY, now=T0, force=True).claimed


def test_failed_run_is_retried_hourly_at_most_three_times(tmp_db_path: Path) -> None:
    now = T0
    for attempt in (1, 2, 3):
        claim = db.claim_job_run(tmp_db_path, DAY, now=now)
        assert (claim.claimed, claim.attempts) == (True, attempt)
        db.finish_job_run(tmp_db_path, DAY, status=db.JOB_FAILED, now=now, error="Boom")
        soon = db.claim_job_run(tmp_db_path, DAY, now=now + timedelta(minutes=10))
        if attempt < 3:
            assert soon.reason == "retry_later"
            assert soon.retry_at == now + timedelta(hours=1)
        now += timedelta(hours=1)

    assert db.claim_job_run(tmp_db_path, DAY, now=now).reason == "exhausted"


def test_crashed_running_row_is_reclaimed_when_stale(tmp_db_path: Path) -> None:
    db.claim_job_run(tmp_db_path, DAY, now=T0)

    assert (
        db.claim_job_run(tmp_db_path, DAY, now=T0 + timedelta(hours=1)).reason
        == "running"
    )
    stale = db.claim_job_run(tmp_db_path, DAY, now=T0 + timedelta(hours=3), force=True)
    assert (stale.claimed, stale.attempts) == (True, 2)


def test_finish_rejects_unknown_status(tmp_db_path: Path) -> None:
    with pytest.raises(ValueError):
        db.finish_job_run(tmp_db_path, DAY, status="running", now=T0)
