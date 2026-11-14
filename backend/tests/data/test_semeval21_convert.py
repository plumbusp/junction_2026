"""Tests for scripts/data/semeval21_convert.py on a tiny invented fixture.

Fixture (backend/tests/fixtures/data/semeval21/): 5 train, 2 dev, 5 test records covering
single, no-label, multi-technique, out-of-taxonomy-only, a dev/test overlap, a dev duplicate,
a repairable bad offset and an ungroundable span.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ._scripts import FIXTURE_DIR, REPO_ROOT, load_script

convert_mod = load_script("semeval21_convert")
check_splits = load_script("check_splits")

RAW = FIXTURE_DIR / "semeval21"


def _read(path: Path) -> dict[str, dict]:
    return {r["id"]: r for r in (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())}


@pytest.fixture
def out(tmp_path: Path) -> dict:
    dev_dir, staging_dir = tmp_path / "dev", tmp_path / "staging"
    manifests = convert_mod.convert(RAW, dev_dir, staging_dir, seed=7, date="2026-01-01")
    return {
        "dev_dir": dev_dir,
        "staging_dir": staging_dir,
        "dev": _read(dev_dir / "semeval21_dev.jsonl"),
        "multi": _read(dev_dir / "semeval21_dev_multi.jsonl"),
        "fewshot": _read(dev_dir / "fewshot.jsonl"),
        "eval": _read(staging_dir / "semeval21_test.jsonl"),
        "dev_manifest": manifests["dev"],
        "eval_manifest": manifests["eval"],
    }


def test_labelling_rules_route_each_example(out: dict) -> None:
    assert {k: v["technique"] for k, v in out["dev"].items()} == {
        "semeval21-train-t1": "loaded_language",
        "semeval21-train-t2": "no_technique",
        "semeval21-dev-d2": "doubt",
    }
    assert out["multi"]["semeval21-train-t3"]["techniques"] == ["doubt", "vague_authority"]
    assert {k: v["technique"] for k, v in out["eval"].items()} == {
        "semeval21-test-s1": "no_technique",
        "semeval21-test-s2": "loaded_language",
        "semeval21-test-s3": "exaggeration_minimization",
    }
    assert out["eval"]["semeval21-test-s1"]["evidence_spans"] == []
    assert out["eval"]["semeval21-test-s1"]["source"] == "semeval21"


def test_exclusions_are_counted_by_reason(out: dict) -> None:
    assert out["eval"].keys().isdisjoint({"semeval21-test-s4", "semeval21-test-s5"})
    assert out["eval_manifest"]["excluded_by_reason"] == {"multi_technique": 1, "out_of_taxonomy_only": 1}
    assert out["dev_manifest"]["excluded_by_reason"] == {
        "duplicate_text_in_split": 1,
        "out_of_taxonomy_only": 1,
        "overlaps_upstream_test": 1,
    }


def test_evidence_only_from_mapped_labels_exact_and_deduplicated(out: dict) -> None:
    # d2 has an out-of-taxonomy "Slogans" span and a repeated Doubt span.
    assert out["dev"]["semeval21-dev-d2"]["evidence_spans"] == ["not safe at all"]
    for rows in (out["dev"], out["eval"], out["fewshot"], out["multi"]):
        for row in rows.values():
            for span in row["evidence_spans"]:
                assert span and span in row["text"]


def test_bad_offsets_are_repaired_or_dropped_and_counted(out: dict) -> None:
    # s2: wrong offsets, fragment present -> repaired; spans ordered by position in the text.
    assert out["eval"]["semeval21-test-s2"]["evidence_spans"] == ["huge scandal", "scandal"]
    # s3: fragment not in text -> span dropped, label (and technique) kept.
    assert out["eval"]["semeval21-test-s3"]["evidence_spans"] == []
    spans = out["eval_manifest"]["spans"]
    assert spans["repaired_from_text_fragment"] == 1
    assert spans["dropped_not_in_text"] == 1
    assert spans["dropped_in_ids"] == ["semeval21-test-s3"]


def test_dev_overlap_removed_from_dev_never_from_eval(out: dict) -> None:
    assert "semeval21-train-t5" not in out["dev"]  # same text as test s1
    assert "semeval21-test-s1" in out["eval"]
    assert "semeval21-dev-d1" not in out["dev"]  # duplicate of t2 after normalization
    report = check_splits.check_splits([out["dev_dir"]], [out["staging_dir"]])
    assert report.ok, report


def test_manifest_records_provenance_and_hashes(out: dict) -> None:
    m = out["eval_manifest"]
    assert m["seed"] == 7 and m["date"] == "2026-01-01"
    assert m["licence"] == "free for general research use"
    assert m["upstream_url"].startswith("https://github.com/di-dimitrov/")
    assert m["counts_per_technique"]["semeval21_test.jsonl"]["no_technique"] == 1
    data = (out["staging_dir"] / "semeval21_test.jsonl").read_bytes()
    assert m["files"]["semeval21_test.jsonl"]["sha256"] == hashlib.sha256(data).hexdigest()


def test_fewshot_comes_from_dev_and_reports_missing_techniques(out: dict) -> None:
    assert set(out["fewshot"]) <= set(out["dev"])
    assert len(out["fewshot"]) <= 14
    assert out["dev_manifest"]["fewshot_missing_techniques"] == [
        "appeal_to_fear",
        "exaggeration_minimization",
        "vague_authority",
        "changing_subject",
    ]


def test_conversion_is_deterministic(tmp_path: Path) -> None:
    a = convert_mod.convert(RAW, tmp_path / "a" / "dev", tmp_path / "a" / "st", seed=7, date="2026-01-01")
    b = convert_mod.convert(RAW, tmp_path / "b" / "dev", tmp_path / "b" / "st", seed=7, date="2026-01-01")
    assert a == b


def test_unknown_label_raises(tmp_path: Path) -> None:
    for name in convert_mod.RAW_FILES.values():
        (tmp_path / name).write_text("[]", encoding="utf-8")
    bad = [{"id": "x", "text": "Plain text here", "labels": [
        {"start": 0, "end": 5, "technique": "Made Up Label", "text_fragment": "Plain"}]}]
    (tmp_path / "test_set_task2.txt").write_text(json.dumps(bad), encoding="utf-8")

    with pytest.raises(ValueError, match="unknown SemEval-2021 label"):
        convert_mod.convert(tmp_path, tmp_path / "dev", tmp_path / "st", date="2026-01-01")


def test_refuses_to_write_under_data_eval(tmp_path: Path) -> None:
    target = REPO_ROOT / "data" / "eval" / "eval_v1"
    existed = target.exists()
    with pytest.raises(SystemExit):
        convert_mod.main(
            ["--raw-dir", str(RAW), "--dev-dir", str(tmp_path / "dev"), "--staging-dir", str(target)]
        )
    assert target.exists() == existed
    assert not (tmp_path / "dev").exists()
