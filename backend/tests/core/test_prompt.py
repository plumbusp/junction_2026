"""Prompt content, few-shot loading and the no-eval-leakage invariant.

The real ``data/eval/`` is never read here. The overlap helper is exercised on a
synthetic eval folder under ``tmp_path``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.core.cache import normalize_text
from app.core.prompt import (
    EVAL_DIR,
    PROMPT_VERSION,
    TECHNIQUE_DEFINITIONS,
    build_messages,
    load_fewshot,
)
from app.core.types import TECHNIQUES


def eval_texts(eval_dir: Path) -> set[str]:
    """Normalized texts of every JSONL record under ``eval_dir`` (overlap checks only)."""
    texts: set[str] = set()
    for path in sorted(eval_dir.rglob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                text = json.loads(line).get("text")
                if isinstance(text, str) and text.strip():
                    texts.add(normalize_text(text).lower())
    return texts


def overlapping(prompt: str, texts: set[str]) -> set[str]:
    haystack = normalize_text(prompt).lower()
    return {text for text in texts if text in haystack}


def write_jsonl(path: Path, records: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def test_prompt_without_fewshot_file_has_definitions_and_rules(tmp_path: Path) -> None:
    examples = load_fewshot(tmp_path / "missing.jsonl")
    system, user = build_messages("Some headline", examples)

    assert examples == []
    assert PROMPT_VERSION == "v4"
    assert set(TECHNIQUE_DEFINITIONS) == set(TECHNIQUES)
    for technique in TECHNIQUES:
        assert technique in system["content"]
    assert "When in doubt, answer no_technique" in system["content"]
    assert "strict JSON" in system["content"]
    assert user == {"role": "user", "content": "<text>Some headline</text>"}


def test_fewshot_file_is_loaded_and_bad_lines_skipped(tmp_path: Path) -> None:
    path = write_jsonl(
        tmp_path / "fewshot.jsonl",
        [
            {
                "id": "d1",
                "text": "Mayor slams rip-off fees",
                "technique": "loaded_language",
                "evidence_spans": ["slams", "invented"],
            },
            {
                "id": "d2",
                "text": "Bad label",
                "technique": "bandwagon",
                "evidence_spans": [],
            },
        ],
    )

    examples = load_fewshot(path)
    system = build_messages("x", examples)[0]["content"]

    assert len(examples) == 1
    assert examples[0].evidence_spans == ["slams"]
    assert "Mayor slams rip-off fees" in system


def test_fewshot_from_eval_dir_is_refused() -> None:
    # Refused by path before any file access.
    with pytest.raises(ValueError, match="data/eval"):
        load_fewshot(EVAL_DIR / "eval_v1" / "test.jsonl")


def test_clean_prompt_has_no_overlap_and_leak_is_detected(tmp_path: Path) -> None:
    fake_eval = tmp_path / "eval"
    write_jsonl(
        fake_eval / "eval_v1" / "test.jsonl",
        [
            {
                "id": "t1",
                "text": "Scientists warn of total collapse",
                "technique": "vague_authority",
            }
        ],
    )
    clean = write_jsonl(
        tmp_path / "dev" / "clean.jsonl",
        [
            {
                "id": "d1",
                "text": "Mayor slams rip-off fees",
                "technique": "loaded_language",
                "evidence_spans": ["slams"],
            }
        ],
    )
    leaked = write_jsonl(
        tmp_path / "dev" / "leaked.jsonl",
        [
            {
                "id": "d2",
                "text": "Scientists  warn of total collapse",
                "technique": "vague_authority",
                "evidence_spans": ["Scientists"],
            }
        ],
    )
    texts = eval_texts(fake_eval)

    clean_prompt = build_messages("x", load_fewshot(clean))[0]["content"]
    leaked_prompt = build_messages("x", load_fewshot(leaked))[0]["content"]

    assert overlapping(clean_prompt, texts) == set()
    assert overlapping(leaked_prompt, texts) == {"scientists warn of total collapse"}
