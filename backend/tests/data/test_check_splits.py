"""Tests for scripts/data/check_splits.py: dev/eval overlap and within-file duplicates."""

from __future__ import annotations

import json
from pathlib import Path

from ._scripts import load_script

check_splits = load_script("check_splits")


def _write(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def _row(rid: str, text: str) -> dict:
    return {"id": rid, "source": "semeval21", "text": text, "technique": "no_technique", "evidence_spans": []}


def test_normalize_text_ignores_case_punctuation_and_whitespace() -> None:
    assert check_splits.normalize_text("  Hello,\n\nWORLD!  ") == "hello world"
    assert check_splits.normalize_text("hello world") == check_splits.normalize_text("Hello -- World...")


def test_clean_split_passes(tmp_path: Path) -> None:
    _write(tmp_path / "dev" / "a.jsonl", [_row("d1", "Bus lines change in May"), _row("d2", "New park opens")])
    _write(tmp_path / "eval" / "b.jsonl", [_row("e1", "Ferry schedule updated")])

    code = check_splits.main(["--dev", str(tmp_path / "dev"), "--eval", str(tmp_path / "eval")])

    assert code == 0


def test_injected_overlap_fails(tmp_path: Path, capsys) -> None:
    _write(tmp_path / "dev" / "a.jsonl", [_row("d1", "Bus lines change in May")])
    _write(tmp_path / "eval" / "b.jsonl", [_row("e1", "Ferry schedule updated"), _row("e2", "bus lines change, in may!")])

    code = check_splits.main(["--dev", str(tmp_path / "dev"), "--eval", str(tmp_path / "eval")])

    assert code == 1
    out = capsys.readouterr().out
    assert "OVERLAP" in out and "d1" in out and "e2" in out
    assert "Bus lines" not in out  # only ids are printed, never texts


def test_duplicate_within_one_file_fails(tmp_path: Path) -> None:
    _write(tmp_path / "dev" / "a.jsonl", [_row("d1", "New park opens"), _row("d2", "NEW PARK OPENS.")])
    _write(tmp_path / "eval" / "b.jsonl", [_row("e1", "Ferry schedule updated")])

    report = check_splits.check_splits([tmp_path / "dev"], [tmp_path / "eval"])

    assert not report.ok
    assert report.within_file_duplicates and not report.cross_overlaps


def test_same_text_in_two_dev_files_is_allowed(tmp_path: Path) -> None:
    # fewshot.jsonl is a subset of the dev set by design.
    _write(tmp_path / "dev" / "dev.jsonl", [_row("d1", "New park opens")])
    _write(tmp_path / "dev" / "fewshot.jsonl", [_row("d1", "New park opens")])
    _write(tmp_path / "eval" / "b.jsonl", [_row("e1", "Ferry schedule updated")])

    assert check_splits.check_splits([tmp_path / "dev"], [tmp_path / "eval"]).ok


def test_missing_eval_side_fails(tmp_path: Path) -> None:
    _write(tmp_path / "dev" / "a.jsonl", [_row("d1", "New park opens")])

    code = check_splits.main(["--dev", str(tmp_path / "dev"), "--eval", str(tmp_path / "nothing_here")])

    assert code == 1
