"""Dev leave-one-out check on fixtures. Offline; data/eval is never read."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.core import classifier, retrieval
from app.core.classifier import classify_live
from app.core.prompt import EVAL_DIR
from app.core.stub import stub_classify
from eval import dev_check
from eval.run import EvalError

from tests.core.fakes import FakeRouter, answer, live_settings

FIXTURE_DEV = Path(__file__).resolve().parents[1] / "fixtures" / "dev"
HEADLINES = FIXTURE_DEV / "headlines_dev.jsonl"


def test_stub_run_writes_summary_under_stub_dir(tmp_path: Path) -> None:
    path = dev_check.run_dev_check(
        mode="retrieval",
        core_mode="stub",
        classify_fn=stub_classify,
        headlines_path=HEADLINES,
        results_root=tmp_path / "dev",
        retrieval_k=4,
    )

    assert path.parent == tmp_path / "dev" / "stub"
    payload = dev_check.json.loads(path.read_text(encoding="utf-8"))
    assert payload["n"] == 7 and payload["n_errors"] == 0
    assert payload["stub_note"] and payload["fewshot_mode"] == "retrieval"
    per = payload["per_technique"]
    assert per["doubt"]["n"] == 1 and per["no_technique"]["recall"]["n"] == 1
    assert set(payload["accuracy"]) == {"k", "n", "value", "ci"}
    assert len(list(tmp_path.rglob("*.json"))) == 1  # nothing else is written


def test_limit_and_static_mode_file_name(tmp_path: Path) -> None:
    path = dev_check.run_dev_check(
        mode="static",
        core_mode="stub",
        classify_fn=stub_classify,
        headlines_path=HEADLINES,
        results_root=tmp_path,
        limit=3,
    )
    assert "headlines-static-" in path.name
    assert dev_check.json.loads(path.read_text(encoding="utf-8"))["n"] == 3


def test_headline_is_never_in_its_own_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(retrieval, "_index", retrieval.build_index(FIXTURE_DEV))
    monkeypatch.setattr(classifier, "load_fewshot", list)
    prompts: list[tuple[str, str]] = []
    real = classifier.build_messages

    def spy(text, examples=None):
        messages = real(text, examples)
        prompts.append((text, messages[0]["content"]))
        return messages

    monkeypatch.setattr(classifier, "build_messages", spy)
    router = FakeRouter({m: answer("no_technique", []) for m in ["model-a", "model-b", "model-c"]})

    def classify_fn(text: str):
        return classify_live(
            text, settings=live_settings(), client=router.client(), cache_dir=tmp_path / "cache"
        )

    path = dev_check.run_dev_check(
        mode="retrieval",
        core_mode="live",
        classify_fn=classify_fn,
        headlines_path=HEADLINES,
        results_root=tmp_path / "results",
        workers=1,
    )

    assert path.parent == tmp_path / "results"  # live results are not in stub/
    assert len(prompts) == 7
    for text, system in prompts:
        assert f"Text: {text}\n" not in system
        assert "Text: " in system  # but other examples were retrieved


def test_eval_paths_and_missing_file_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="data/eval"):
        dev_check.load_headlines(EVAL_DIR / "eval_v1" / "headlines.jsonl")
    with pytest.raises(ValueError, match="data/eval"):
        dev_check.run_dev_check(
            mode="retrieval",
            core_mode="stub",
            classify_fn=stub_classify,
            headlines_path=HEADLINES,
            results_root=EVAL_DIR / "x",
        )
    with pytest.raises(EvalError, match="not found"):
        dev_check.load_headlines(tmp_path / "missing.jsonl")
