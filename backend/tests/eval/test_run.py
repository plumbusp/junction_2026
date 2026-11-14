"""Runner tests on a synthetic frozen eval version in tmp_path (data/eval is never read)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.core.stub import stub_classify
from app.core.types import ModelVote, Verdict
from eval import run

NOW = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)

ROWS = [
    {"id": "s-1", "source": "semeval21", "text": "Crooked thugs ruin the city", "technique": "loaded_language", "evidence_spans": ["Crooked thugs"]},
    {"id": "s-2", "source": "semeval21", "text": "Council approves new budget", "technique": "no_technique", "evidence_spans": []},
    {"id": "s-3", "source": "semeval21", "text": "Experts say it is the worst crisis ever", "technique": "exaggeration_minimization", "evidence_spans": ["the worst crisis ever"]},
    {"id": "s-4", "source": "semeval21", "text": "Rain expected on Tuesday", "technique": "no_technique", "evidence_spans": []},
    {"id": "g-1", "source": "gold", "text": "Mayor slams rip-off fares", "technique": "loaded_language", "evidence_spans": ["slams", "rip-off"]},
]


def make_eval_version(root: Path, version: str = "eval_t") -> Path:
    vdir = root / version
    vdir.mkdir(parents=True)
    data = "".join(json.dumps(r) + "\n" for r in ROWS)
    (vdir / "test.jsonl").write_text(data, encoding="utf-8")
    manifest = {
        "source": "semeval21",
        "excluded_by_reason": {"multi_technique": 3, "out_of_taxonomy_only": 2},
        "files": {"test.jsonl": {"sha256": hashlib.sha256(data.encode()).hexdigest(), "n": len(ROWS)}},
    }
    (vdir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def fake_live_classify(text: str) -> Verdict:
    """Non-stub verdict: three named models agree on no_technique, except one failure."""
    if "Tuesday" in text:
        raise RuntimeError("model call failed")
    votes = [ModelVote(m, "no_technique", [], "x") for m in ("model-a", "model-b", "model-c")]
    return Verdict("no_technique", [], "x", 3, 3, False, votes)


def all_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()]


def test_freeze_check_passes_on_unmodified_copy(tmp_path: Path) -> None:
    data_root = make_eval_version(tmp_path / "data")
    manifest, sha = run.check_freeze(data_root / "eval_t")
    assert manifest["source"] == "semeval21"
    assert sha == hashlib.sha256((data_root / "eval_t" / "manifest.json").read_bytes()).hexdigest()


def test_freeze_check_fails_on_modified_file_and_writes_nothing(tmp_path: Path) -> None:
    data_root = make_eval_version(tmp_path / "data")
    path = data_root / "eval_t" / "test.jsonl"
    path.write_text(path.read_text(encoding="utf-8").replace("Rain", "Snow"), encoding="utf-8")
    with pytest.raises(run.FreezeError, match="test.jsonl: sha256"):
        run.check_freeze(data_root / "eval_t")

    results_root = tmp_path / "results"
    code = run.main(["--eval-version", "eval_t", "--data-dir", str(data_root), "--results-dir", str(results_root)])
    assert code != 0
    assert not results_root.exists()


def test_freeze_check_fails_on_unlisted_file(tmp_path: Path) -> None:
    data_root = make_eval_version(tmp_path / "data")
    (data_root / "eval_t" / "extra.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(run.FreezeError, match="not listed"):
        run.check_freeze(data_root / "eval_t")


def test_stub_run_writes_only_under_stub(tmp_path: Path) -> None:
    data_root = make_eval_version(tmp_path / "data")
    results_root = tmp_path / "results"
    paths = run.run_eval("eval_t", mode="stub", classify_fn=stub_classify, data_root=data_root, results_root=results_root, now=NOW)
    files = all_files(results_root)
    assert files and all(p.parent == results_root / "stub" for p in files)
    assert not (results_root / "latest.json").exists()
    assert "latest" not in paths
    payload = json.loads(paths["results"].read_text(encoding="utf-8"))
    assert payload["mode"] == "stub" and payload["stub_note"]
    # Sources are kept apart, never merged.
    assert set(payload["sources"]) == {"semeval21", "gold"}
    assert payload["sources"]["semeval21"]["n"] == 4 and payload["sources"]["gold"]["n"] == 1


def test_live_run_counts_errors_and_updates_latest(tmp_path: Path) -> None:
    data_root = make_eval_version(tmp_path / "data")
    results_root = tmp_path / "results"
    paths = run.run_eval("eval_t", mode="live", classify_fn=fake_live_classify, data_root=data_root, results_root=results_root, workers=2, now=NOW)
    assert paths["results"].parent == results_root
    assert (results_root / "latest.json").read_bytes() == paths["results"].read_bytes()
    payload = json.loads(paths["results"].read_text(encoding="utf-8"))
    assert payload["core_models"] == ["model-a", "model-b", "model-c"]
    src = payload["sources"]["semeval21"]
    assert (src["n"], src["n_errors"], src["error_ids"]) == (3, 1, ["s-4"])
    preds = [json.loads(line) for line in paths["predictions"].read_text(encoding="utf-8").splitlines()]
    assert [p["id"] for p in preds] == [r["id"] for r in ROWS]
    assert preds[3]["error"] and preds[3]["ensemble"] is None


def test_live_run_refuses_stub_verdicts(tmp_path: Path) -> None:
    data_root = make_eval_version(tmp_path / "data")
    results_root = tmp_path / "results"
    with pytest.raises(run.EvalError, match="stub verdicts"):
        run.run_eval("eval_t", mode="live", classify_fn=stub_classify, data_root=data_root, results_root=results_root, now=NOW)
    assert not results_root.exists()


def test_results_json_has_the_keys_metrics_page_reads(tmp_path: Path) -> None:
    """Keys read by frontend/assets/metrics.js (and the shape of frontend/mock/metrics.json)."""
    data_root = make_eval_version(tmp_path / "data")
    paths = run.run_eval("eval_t", mode="stub", classify_fn=stub_classify, data_root=data_root, results_root=tmp_path / "r", now=NOW)
    payload = json.loads(paths["results"].read_text(encoding="utf-8"))

    def is_proportion(p: dict) -> bool:
        return set(p) >= {"k", "n", "value", "ci"}

    top = {"mode", "stub_note", "run_at", "prompt_version", "eval_version", "manifest_sha256", "core_models", "techniques", "known_limitations", "sources"}
    assert top <= set(payload)
    assert isinstance(payload["known_limitations"], list) and payload["known_limitations"]
    src = payload["sources"]["semeval21"]
    assert {"label", "n", "n_excluded_out_of_taxonomy", "accuracy", "macro_f1", "fpr_no_technique", "per_technique", "confusion_matrix", "span_token_f1", "by_agreement", "flag_policies", "per_model", "human_ceiling"} <= set(src)
    assert src["n_excluded_out_of_taxonomy"] == 2
    assert is_proportion(src["accuracy"]) and is_proportion(src["fpr_no_technique"])
    assert {"value", "ci", "labels"} <= set(src["macro_f1"])
    for row in src["per_technique"].values():
        assert {"support", "predicted", "tp", "f1"} <= set(row)
        assert is_proportion(row["precision"]) and is_proportion(row["recall"])
    cm = src["confusion_matrix"]
    assert cm["rows"] == "reference" and cm["columns"] == "predicted" and len(cm["matrix"]) == len(cm["labels"]) == 7
    assert {"mean", "n"} <= set(src["span_token_f1"])
    assert [lv["level"] for lv in src["by_agreement"]] == ["3/3", "2/3", "disputed"]
    assert all(is_proportion(lv["accuracy"]) for lv in src["by_agreement"])
    for policy in src["flag_policies"]:
        assert {"policy", "label"} <= set(policy)
        assert is_proportion(policy["fpr_no_technique"]) and is_proportion(policy["recall_any_technique"])
    assert src["per_model"][-1]["model"] == "ensemble"
    for model in src["per_model"]:
        assert is_proportion(model["accuracy"]) and is_proportion(model["fpr_no_technique"])
        assert model["macro_f1"] is None or isinstance(model["macro_f1"], float)
