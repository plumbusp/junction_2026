"""Live classification path with a mocked AI Router. No test touches the network."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import pytest
from app.core.cache import cache_key
from app.core.classifier import classify, classify_live, parse_vote
from app.core.clients import ModelCallError
from app.core.prompt import PROMPT_VERSION, FewShotExample, fewshot_hash

from tests.core.fakes import FAKE_KEY, FakeRouter, answer, live_settings

TEXT = "Mayor slams rip-off parking fees as experts warn of gridlock"


def run(
    router: FakeRouter,
    tmp_path: Path,
    text: str = TEXT,
    examples: list[FewShotExample] | None = None,
):
    return classify_live(
        text,
        settings=live_settings(),
        client=router.client(),
        cache_dir=tmp_path,
        examples=examples if examples is not None else [],
    )


def test_majority_win_through_mocked_router(tmp_path: Path) -> None:
    router = FakeRouter(
        {
            "model-a": answer(
                "loaded_language", ["slams", "rip-off"], "Charged words."
            ),
            "model-b": answer("loaded_language", ["slams"]),
            "model-c": answer("vague_authority", ["experts warn"]),
        }
    )

    verdict = run(router, tmp_path)

    assert verdict.technique == "loaded_language"
    assert (verdict.agreement, verdict.n_models, verdict.disputed) == (2, 3, False)
    assert verdict.evidence_spans == ["slams"]
    assert verdict.explanation == "Charged words."
    assert [v.model for v in verdict.votes] == ["model-a", "model-b", "model-c"]


def test_invented_span_is_dropped_not_fixed(tmp_path: Path) -> None:
    router = FakeRouter(
        {
            m: answer("loaded_language", ["slams", "Slams", "outrageous rip-off"])
            for m in ["model-a", "model-b", "model-c"]
        }
    )

    verdict = run(router, tmp_path)

    assert verdict.evidence_spans == ["slams"]
    for v in verdict.votes:
        assert v.evidence_spans == ["slams"]
        assert all(span in TEXT for span in v.evidence_spans)


def test_malformed_json_discards_one_vote(tmp_path: Path) -> None:
    router = FakeRouter(
        {
            "model-a": answer("appeal_to_fear", ["gridlock"]),
            "model-b": "Sure! The technique is appeal_to_fear.",
            "model-c": answer("appeal_to_fear", ["gridlock"]),
        }
    )

    verdict = run(router, tmp_path)

    assert [v.model for v in verdict.votes] == ["model-a", "model-c"]
    assert (verdict.technique, verdict.agreement, verdict.disputed) == (
        "appeal_to_fear",
        2,
        False,
    )
    assert verdict.evidence_spans == ["gridlock"]


def test_unknown_technique_discards_vote_and_log_has_no_model_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_ish = "UNIQUE-MODEL-CONTENT-123"
    raw = answer("bandwagon", [], secret_ish)

    with caplog.at_level(logging.INFO):
        assert parse_vote("model-a", raw, TEXT) is None
        assert (
            parse_vote("model-a", "```json\n" + answer("doubt", []) + "\n```", TEXT)
            is not None
        )

    assert "unknown technique" in caplog.text
    assert secret_ish not in caplog.text


def test_model_timing_out_is_excluded_after_one_retry(tmp_path: Path) -> None:
    router = FakeRouter(
        {
            "model-a": answer("doubt", ["experts warn"]),
            "model-b": "timeout",
            "model-c": answer("doubt", ["experts warn"]),
        }
    )

    verdict = run(router, tmp_path)

    assert router.calls["model-b"] == 2  # first attempt + one retry
    assert [v.model for v in verdict.votes] == ["model-a", "model-c"]
    assert (verdict.technique, verdict.agreement, verdict.n_models) == ("doubt", 2, 3)
    assert list(tmp_path.glob("*.json")) == []  # transient failure is not cached


def test_5xx_is_retried_once_then_succeeds(tmp_path: Path) -> None:
    router = FakeRouter(
        {
            "model-a": [503, answer("no_technique", [])],
            "model-b": answer("no_technique", []),
            "model-c": answer("no_technique", []),
        }
    )

    verdict = run(router, tmp_path)

    assert router.calls["model-a"] == 2
    assert (verdict.technique, verdict.agreement) == ("no_technique", 3)


def test_only_one_successful_vote_is_disputed(tmp_path: Path) -> None:
    router = FakeRouter(
        {"model-a": answer("doubt", []), "model-b": "timeout", "model-c": 500}
    )

    verdict = run(router, tmp_path)

    assert (verdict.technique, verdict.disputed, len(verdict.votes)) == (
        "no_technique",
        True,
        1,
    )


def test_cache_hit_returns_stored_verdict_without_calls(tmp_path: Path) -> None:
    replies = {
        m: answer("appeal_to_fear", ["gridlock"])
        for m in ["model-a", "model-b", "model-c"]
    }
    first_router = FakeRouter(replies)
    first = run(first_router, tmp_path)
    assert len(list(tmp_path.glob("*.json"))) == 1

    second_router = FakeRouter(replies)
    second = run(
        second_router, tmp_path, text="  " + TEXT + "\n"
    )  # same normalized text

    assert sum(second_router.calls.values()) == 0
    assert second == first


def test_changing_fewshot_examples_changes_cache_key(tmp_path: Path) -> None:
    example = FewShotExample(
        text="Mayor slams fees", technique="loaded_language", evidence_spans=["slams"]
    )
    models = ["model-a", "model-b", "model-c"]
    key_none = cache_key(PROMPT_VERSION, TEXT, models, fewshot_hash([]))
    key_one = cache_key(PROMPT_VERSION, TEXT, models, fewshot_hash([example]))

    assert fewshot_hash([]) == hashlib.sha256(b"").hexdigest()
    assert key_none != key_one

    # End to end: a verdict cached under the old few-shot set is not reused.
    replies = {m: answer("loaded_language", ["slams"]) for m in models}
    run(FakeRouter(replies), tmp_path, examples=[])
    second_router = FakeRouter(replies)
    run(second_router, tmp_path, examples=[example])

    assert sum(second_router.calls.values()) == 3
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_http_error_message_and_logs_never_contain_key(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    router = FakeRouter({"model-a": 401, "model-b": 401, "model-c": 401})

    with caplog.at_level(logging.DEBUG):
        verdict = run(router, tmp_path)
        with pytest.raises(ModelCallError) as excinfo:
            router.client().complete("model-a", [])

    assert verdict.disputed is True
    assert FAKE_KEY not in str(excinfo.value)
    assert FAKE_KEY not in caplog.text


def test_classify_rejects_empty_text() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        classify("   ")
