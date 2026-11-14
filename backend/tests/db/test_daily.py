"""Daily challenge storage: schema migration, selection rule and per-date job idempotency."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app import db

from tests.db.rounds_fixture import (
    LIVE_MODELS,
    seed_round_with_techniques,
    seed_simple_round,
)

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


def test_old_database_gets_diversity_columns_backfilled_from_stored_references(
    tmp_path: Path,
) -> None:
    path = tmp_path / "old2.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE rounds (round_id TEXT PRIMARY KEY, cluster_id TEXT, event TEXT NOT NULL,"
        " event_date TEXT NOT NULL, source_note TEXT NOT NULL DEFAULT '',"
        " core_mode TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,"
        " n_outlets INTEGER);"
        "INSERT INTO rounds VALUES ('r-old', 'c', 'Old', '2026-11-01', '', 'stub', 'ready', 'x', 3);"
        "CREATE TABLE round_items (item_id TEXT PRIMARY KEY, round_id TEXT NOT NULL,"
        " reference TEXT NOT NULL, position INTEGER);"
    )
    for n, (technique, position) in enumerate(
        [("no_technique", 0), ("doubt", 1), ("doubt", 2), ("loaded_language", None)]
    ):
        conn.execute(
            "INSERT INTO round_items VALUES (?, 'r-old', ?, ?)",
            (f"i{n}", f'{{"technique": "{technique}"}}', position),
        )
    conn.commit()
    conn.close()

    db.init_db(path)
    db.init_db(path)  # idempotent

    assert db.get_round_diversity(path, "r-old") == 2  # the out-of-play item is ignored
    with db.connect(path) as c:
        stored = c.execute("SELECT techniques FROM rounds").fetchone()[0]
    assert json.loads(stored) == ["doubt", "no_technique"]


def test_insert_stores_diversity_and_sorted_techniques(tmp_db_path: Path) -> None:
    mixed = seed_round_with_techniques(
        tmp_db_path, "m", ("loaded_language", "no_technique", "doubt")
    )
    flat = seed_round_with_techniques(
        tmp_db_path, "f", ("no_technique", "no_technique", "no_technique")
    )

    assert (mixed.diversity, flat.diversity) == (3, 1)
    assert db.get_round_diversity(tmp_db_path, mixed.round_id) == 3
    with db.connect(tmp_db_path) as c:
        row = c.execute(
            "SELECT techniques FROM rounds WHERE round_id = ?", (mixed.round_id,)
        ).fetchone()
    assert json.loads(row[0]) == ["doubt", "loaded_language", "no_technique"]


def test_daily_pick_prefers_diversity_over_outlets(tmp_db_path: Path) -> None:
    wide_flat = seed_round_with_techniques(
        tmp_db_path, "flat", ("no_technique", "loaded_language", "loaded_language"), n_outlets=9
    )
    diverse = seed_round_with_techniques(
        tmp_db_path, "diverse", ("no_technique", "loaded_language", "doubt"), n_outlets=3
    )

    picked = db.pick_daily_challenge(
        tmp_db_path, DAY, [wide_flat.round_id, diverse.round_id], "stub"
    )

    assert picked == diverse.round_id


def test_daily_pick_prefers_techniques_not_used_in_the_last_seven_dailies(
    tmp_db_path: Path,
) -> None:
    yesterday = seed_round_with_techniques(
        tmp_db_path, "y", ("no_technique", "loaded_language", "appeal_to_fear")
    )
    repeat = seed_round_with_techniques(
        tmp_db_path, "repeat", ("no_technique", "loaded_language", "appeal_to_fear"), n_outlets=9
    )
    fresh = seed_round_with_techniques(
        tmp_db_path, "fresh", ("no_technique", "doubt", "vague_authority"), n_outlets=3
    )
    db.pick_daily_challenge(tmp_db_path, "2026-11-13", [yesterday.round_id], "stub")

    picked = db.pick_daily_challenge(
        tmp_db_path, DAY, [repeat.round_id, fresh.round_id], "stub"
    )

    assert picked == fresh.round_id  # same diversity, two new techniques beat 9 outlets


def test_daily_pick_still_sets_the_day_when_no_round_has_three_techniques(
    tmp_db_path: Path,
) -> None:
    flat = seed_round_with_techniques(
        tmp_db_path, "flat", ("no_technique", "no_technique", "no_technique"), n_outlets=9
    )
    two = seed_round_with_techniques(
        tmp_db_path, "two", ("no_technique", "doubt", "doubt"), n_outlets=3
    )

    picked = db.pick_daily_challenge(tmp_db_path, DAY, [flat.round_id, two.round_id], "stub")

    assert picked == two.round_id
    assert db.get_round_diversity(tmp_db_path, picked) == 2
    only_flat = db.pick_daily_challenge(tmp_db_path, "2026-11-15", [flat.round_id], "stub")
    assert db.get_round_diversity(tmp_db_path, only_flat) == 1


def test_reset_day_deletes_only_unanswered_rounds_of_the_day(
    tmp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(db, "_now", lambda: "2026-11-14T10:00:00Z")
    techniques = ("no_technique", "doubt", "loaded_language")
    unanswered = seed_round_with_techniques(tmp_db_path, "un", techniques)
    answered = seed_round_with_techniques(tmp_db_path, "an", techniques)
    live = seed_simple_round(tmp_db_path, "live", models=LIVE_MODELS)
    item = db.get_round(tmp_db_path, answered.round_id).items[0]
    db.insert_answer(
        tmp_db_path,
        player_id="p1",
        round_id=answered.round_id,
        item_id=item.item_id,
        technique="doubt",
        selected_words=[],
        technique_points=0,
        evidence_points=0,
        total=0,
    )
    db.pick_daily_challenge(tmp_db_path, DAY, [unanswered.round_id], "stub")

    result = db.reset_day(
        tmp_db_path,
        DAY,
        created_from=T0,
        created_to=T0 + timedelta(days=1),
        core_mode="stub",
    )

    assert (result.deleted, result.kept) == (1, 1)
    assert result.kept_round_ids == {answered.round_id}
    assert db.get_daily_challenge(tmp_db_path, DAY) is None
    assert db.get_round(tmp_db_path, unanswered.round_id) is None
    assert db.get_round(tmp_db_path, answered.round_id) is not None
    assert db.get_round(tmp_db_path, live.round_id) is not None  # other core mode
    with db.connect(tmp_db_path) as c:
        left = c.execute(
            "SELECT COUNT(*) FROM round_items WHERE round_id = ?", (unanswered.round_id,)
        ).fetchone()[0]
    assert left == 0


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
