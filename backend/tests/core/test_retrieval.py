"""Retrieval of few-shot examples. Offline; fixtures only (data/eval is never read)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.core import retrieval
from app.core.prompt import EVAL_DIR, FewShotExample
from app.core.retrieval import build_index, examples_for_text, select_examples

FIXTURE_DEV = Path(__file__).resolve().parents[1] / "fixtures" / "dev"


def ex(text: str, technique: str = "loaded_language", spans: list[str] | None = None):
    return FewShotExample(text=text, technique=technique, evidence_spans=spans or [])


def write_dev(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


@pytest.fixture(scope="module")
def index() -> retrieval.RetrievalIndex:
    return build_index(FIXTURE_DEV)


def test_index_reads_labelled_files_and_skips_multi_label(index) -> None:
    assert len(index) == 11  # 4 semeval + 7 headlines; the multi-label file adds nothing


def test_nearest_example_is_picked_with_spans_as_stored(index) -> None:
    got = select_examples("Mayor slams parking fees", k=1, anchors=[], index=index)

    assert [e.text for e in got] == ["Mayor slams rip-off parking fees"]
    assert got[0].technique == "loaded_language"
    assert got[0].evidence_spans == ["slams", "rip-off"]


def test_anchors_come_first_deduplicated_and_not_repeated(index) -> None:
    a1 = ex("Anchor one")
    a2 = ex("Mayor slams rip-off parking fees")  # also the nearest dev example
    dup = ex("  anchor   ONE ")
    got = select_examples("Mayor slams parking fees", k=2, anchors=[a1, a2, dup], index=index)

    assert got[:2] == [a1, a2]
    texts = [e.text for e in got]
    assert len(got) == 4 and len(set(texts)) == 4
    assert texts.count("Mayor slams rip-off parking fees") == 1


def test_k_zero_gives_anchors_only_without_touching_the_index(monkeypatch) -> None:
    def boom():
        raise AssertionError("index must not be built for k=0")

    monkeypatch.setattr(retrieval, "get_index", boom)
    anchors = [ex("A"), ex("B")]

    assert select_examples("anything", k=0, anchors=anchors) == anchors
    assert examples_for_text("anything", mode="static", k=4, anchors=anchors) == anchors


def test_input_text_is_never_its_own_example(index) -> None:
    own = ex("mayor  SLAMS rip-off parking fees")  # an anchor equal to the input
    got = select_examples(
        "Mayor slams rip-off parking fees\n", k=3, anchors=[own, ex("Other")], index=index
    )

    texts = [" ".join(e.text.lower().split()) for e in got]
    assert texts[0] == "other"
    assert "mayor slams rip-off parking fees" not in texts
    assert len(got) == 3  # "Other" plus the only two other examples sharing words


def test_headline_boost_breaks_near_ties_toward_news(tmp_path: Path) -> None:
    # Same score by construction: the two texts differ by one equally rare word.
    write_dev(
        tmp_path / "dev.jsonl",
        [
            {"id": "a-meme", "source": "semeval21", "text": "mayor slams parking fees today",
             "technique": "loaded_language", "evidence_spans": []},
            {"id": "b-news", "source": "headlines_llm", "text": "mayor slams parking fees tonight",
             "technique": "loaded_language", "evidence_spans": []},
        ],
    )
    idx = build_index(tmp_path)
    query = "mayor slams parking fees"

    plain = select_examples(query, k=1, anchors=[], index=idx, headline_boost=0.0)
    boosted = select_examples(query, k=1, anchors=[], index=idx)

    assert plain[0].text.endswith("today")  # tie broken by id
    assert boosted[0].text.endswith("tonight")  # default boost lets the headline win
    assert 0 < retrieval.HEADLINE_BOOST < 0.2


def test_headline_boost_does_not_beat_a_clearly_closer_meme(tmp_path: Path) -> None:
    write_dev(
        tmp_path / "dev.jsonl",
        [
            {"id": "a-meme", "source": "semeval21", "text": "mayor slams parking fees again",
             "technique": "loaded_language", "evidence_spans": []},
            {"id": "b-news", "source": "headlines_llm", "text": "mayor praises new bus lanes",
             "technique": "no_technique", "evidence_spans": []},
        ],
    )
    got = select_examples(
        "mayor slams parking fees", k=1, anchors=[], index=build_index(tmp_path)
    )
    assert got[0].text == "mayor slams parking fees again"


def test_selection_is_deterministic(index) -> None:
    runs = [
        select_examples("budget for parking and experts", k=5, anchors=[ex("A")], index=index)
        for _ in range(3)
    ]
    assert runs[0] == runs[1] == runs[2]


def test_no_dev_files_gives_anchors_only(tmp_path: Path) -> None:
    empty = build_index(tmp_path / "does-not-exist")
    anchors = [ex("A")]

    assert len(empty) == 0
    assert select_examples("text", k=4, anchors=anchors, index=empty) == anchors
    assert select_examples("text", k=4, anchors=[], index=build_index(tmp_path)) == []


def test_eval_paths_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="data/eval"):
        build_index(EVAL_DIR / "eval_v1")
    with pytest.raises(ValueError, match="data/eval"):
        build_index(EVAL_DIR)


def test_settings_defaults_and_validation() -> None:
    from app.config import load_settings

    default = load_settings({})
    assert (default.fewshot_mode, default.retrieval_k) == ("retrieval", 4)
    assert load_settings({"FEWSHOT_MODE": "Static", "RETRIEVAL_K": "0"}).retrieval_k == 0
    with pytest.raises(ValueError, match="FEWSHOT_MODE"):
        load_settings({"FEWSHOT_MODE": "random"})
    with pytest.raises(ValueError, match="RETRIEVAL_K"):
        load_settings({"RETRIEVAL_K": "13"})


def test_k_out_of_range_is_rejected(index) -> None:
    with pytest.raises(ValueError):
        select_examples("x", k=13, anchors=[], index=index)
    with pytest.raises(ValueError):
        select_examples("x", k=-1, anchors=[], index=index)
