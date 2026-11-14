"""scripts/rounds/seed_from_candidates.py on the offline news dry run (no network, stub core)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from app import db
from app.news import job

from tests.db.rounds_fixture import stub_verdict

SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "scripts"
    / "rounds"
    / "seed_from_candidates.py"
)


def load_seed_script() -> ModuleType:
    name = "scripts_rounds_seed_from_candidates"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _dry_run_candidates(tmp_path: Path) -> Path:
    return job.run_dry(out_dir=tmp_path / "news").path


def test_seed_with_stub_core_stores_every_candidate_event(tmp_path: Path) -> None:
    seed = load_seed_script()
    records = seed.read_candidates(_dry_run_candidates(tmp_path))
    db_path = tmp_path / "rounds.db"

    results = seed.seed(records, db_path)

    assert len(results) == len(records) > 0
    assert all(r.status in (db.STATUS_READY, db.STATUS_INELIGIBLE) for r in results)
    for r in results:
        stored = db.get_round(db_path, r.round_id)
        assert stored.core_mode == "stub"
        assert stored.event.startswith("Keywords: ")
        assert "fictional" in stored.source_note


def test_seed_makes_a_ready_round_when_one_headline_is_clean(tmp_path: Path) -> None:
    seed = load_seed_script()
    records = seed.read_candidates(_dry_run_candidates(tmp_path))[:1]
    first_title = records[0]["headlines"][0]["title"]

    def fake_classify(text: str):
        if text == first_title:
            return stub_verdict("no_technique", [])
        return stub_verdict("loaded_language", [text.split()[0]])

    results = seed.seed(records, tmp_path / "rounds.db", classify_fn=fake_classify)

    assert [r.status for r in results] == [db.STATUS_READY]
    assert results[0].n_in_play == 3
