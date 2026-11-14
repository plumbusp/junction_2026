"""The /api/analyze daily counter: atomic cap, release, 0 = unlimited, guarded migration."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from app import db
from app.config import load_settings


def test_reserve_stops_at_the_cap_and_release_gives_a_slot_back(tmp_db_path: Path) -> None:
    taken = [db.analyze_reserve(tmp_db_path, "2026-11-15", 2) for _ in range(3)]

    assert taken == [True, True, False]
    assert db.analyze_count(tmp_db_path, "2026-11-15") == 2
    db.analyze_release(tmp_db_path, "2026-11-15")
    assert db.analyze_reserve(tmp_db_path, "2026-11-15", 2) is True
    assert db.analyze_reserve(tmp_db_path, "2026-11-16", 2) is True  # a new day starts at 0


def test_release_never_goes_below_zero(tmp_db_path: Path) -> None:
    db.analyze_release(tmp_db_path, "2026-11-15")

    assert db.analyze_count(tmp_db_path, "2026-11-15") == 0


def test_cap_zero_is_unlimited_and_stores_nothing(tmp_db_path: Path) -> None:
    assert all(db.analyze_reserve(tmp_db_path, "2026-11-15", 0) for _ in range(10))
    assert db.analyze_count(tmp_db_path, "2026-11-15") == 0


def test_older_database_gains_the_table_on_init(tmp_db_path: Path) -> None:
    db.init_db(tmp_db_path)
    conn = sqlite3.connect(tmp_db_path)
    conn.execute("DROP TABLE analyze_daily")
    conn.commit()
    conn.close()

    db.init_db(tmp_db_path)  # what the app does at startup

    assert db.analyze_reserve(tmp_db_path, "2026-11-15", 1) is True


def test_setting_defaults_and_validation() -> None:
    assert load_settings({}).analyze_daily_cap == 500
    assert load_settings({"ANALYZE_DAILY_CAP": "0"}).analyze_daily_cap == 0
    assert load_settings({"ANALYZE_DAILY_CAP": " 40 "}).analyze_daily_cap == 40
    with pytest.raises(ValueError, match="ANALYZE_DAILY_CAP"):
        load_settings({"ANALYZE_DAILY_CAP": "-1"})
    with pytest.raises(ValueError, match="ANALYZE_DAILY_CAP"):
        load_settings({"ANALYZE_DAILY_CAP": "many"})
