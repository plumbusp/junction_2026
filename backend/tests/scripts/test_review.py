"""Dispute review tool: state changes, dev-only export, no eval writes, no text in output."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from app import db
from app.tools import disputes as tool

from tests.db.disputes_fixture import COMMENT, dispute, items_of

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "disputes" / "review.py"


def run(db_path: Path, *argv: str) -> int:
    return tool.main(["--db-path", str(db_path), *argv])


@pytest.fixture
def item(tmp_db_path: Path) -> db.RoundItem:
    items = items_of(tmp_db_path, "review")
    return next(i for i in items if i.reference.technique == "loaded_language")


def _in_review(db_path: Path, item: db.RoundItem) -> str:
    return dispute(db_path, item, ["doubt", "doubt", "appeal_to_fear"], status="in_review")


def _rescored(db_path: Path, item: db.RoundItem, spans: str = "slams") -> str:
    did = _in_review(db_path, item)
    assert run(db_path, "rescore", did, "--technique", "doubt", "--spans", spans) == 0
    return did


def test_list_shows_ids_and_counts_but_no_comment_or_headline(
    tmp_db_path: Path, item: db.RoundItem, capsys
) -> None:
    did = _in_review(tmp_db_path, item)
    dispute(tmp_db_path, items_of(tmp_db_path, "other")[0], ["doubt"])

    assert run(tmp_db_path, "list", "--status", "in_review") == 0

    out = capsys.readouterr().out
    assert did in out and "votes 3/3" in out and "doubt x2" in out
    assert "1 dispute(s) with status in_review" in out
    assert COMMENT not in out and item.text not in out


def test_list_rejects_an_unknown_status(tmp_db_path: Path, capsys) -> None:
    assert run(tmp_db_path, "list", "--status", "maybe") == 2
    assert "unknown status" in capsys.readouterr().err


def test_escalate_moves_queued_to_in_review_once(
    tmp_db_path: Path, item: db.RoundItem, capsys
) -> None:
    did = dispute(tmp_db_path, item, ["doubt"])

    assert run(tmp_db_path, "escalate", did) == 0
    assert db.get_dispute_by_id(tmp_db_path, did).status == "in_review"
    assert run(tmp_db_path, "escalate", did) == 2  # already in review
    assert run(tmp_db_path, "escalate", "d-missing") == 2
    assert "no dispute with id" in capsys.readouterr().err


def test_rescore_records_the_verdict_for_dev_only(
    tmp_db_path: Path, item: db.RoundItem, capsys
) -> None:
    did = _in_review(tmp_db_path, item)

    assert run(tmp_db_path, "rescore", did, "--technique", "doubt", "--spans", "slams|rip-off") == 0

    row = db.list_dispute_summaries(tmp_db_path, "rescored")[0]
    assert (row.new_technique, row.new_evidence_spans) == ("doubt", ["slams", "rip-off"])
    assert "target set: dev" in capsys.readouterr().out


def test_rescore_refuses_bad_requests(
    tmp_db_path: Path, item: db.RoundItem, capsys
) -> None:
    queued = dispute(tmp_db_path, item, ["doubt"])
    assert run(tmp_db_path, "rescore", queued, "--technique", "doubt") == 2  # not in review

    in_review = _in_review(tmp_db_path, items_of(tmp_db_path, "two")[0])
    assert run(tmp_db_path, "rescore", in_review, "--technique", "propaganda") == 2
    assert run(tmp_db_path, "rescore", in_review, "--technique", "doubt", "--spans", "not in text") == 2
    assert run(tmp_db_path, "rescore", in_review, "--technique", "no_technique", "--spans", "x") == 2
    assert db.get_dispute_by_id(tmp_db_path, in_review).status == "in_review"
    capsys.readouterr()


def test_reject_closes_without_a_label(tmp_db_path: Path, item: db.RoundItem) -> None:
    did = _in_review(tmp_db_path, item)

    assert run(tmp_db_path, "reject", did) == 0
    assert db.get_dispute_by_id(tmp_db_path, did).status == "rejected"
    assert run(tmp_db_path, "reject", did) == 2


def test_export_writes_a_dev_example_from_the_headline_not_the_comment(
    tmp_db_path: Path, item: db.RoundItem, tmp_path: Path
) -> None:
    did = _rescored(tmp_db_path, item)
    out = tmp_path / "dev" / "disputes_confirmed.jsonl"

    assert run(tmp_db_path, "export-dev", "--out", str(out)) == 0

    records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert records == [
        {
            "id": f"dispute-{did}",
            "source": "dispute",
            "text": item.text,
            "technique": "doubt",
            "evidence_spans": ["slams"],
        }
    ]
    assert COMMENT not in out.read_text(encoding="utf-8")


def test_export_is_idempotent(tmp_db_path: Path, item: db.RoundItem, tmp_path: Path) -> None:
    _rescored(tmp_db_path, item)
    out = tmp_path / "confirmed.jsonl"

    run(tmp_db_path, "export-dev", "--out", str(out))
    first = out.read_bytes()
    run(tmp_db_path, "export-dev", "--out", str(out))

    assert out.read_bytes() == first


def test_export_leaves_out_unrescored_and_stub_round_items(
    tmp_db_path: Path, item: db.RoundItem, tmp_path: Path
) -> None:
    _in_review(tmp_db_path, item)  # not rescored yet
    stub_item = items_of(tmp_db_path, "stubby", live=False)[0]
    _rescored(tmp_db_path, stub_item, spans="")
    out = tmp_path / "confirmed.jsonl"

    run(tmp_db_path, "export-dev", "--out", str(out))
    assert not out.exists()
    run(tmp_db_path, "export-dev", "--out", str(out), "--include-stub")

    assert len(out.read_text(encoding="utf-8").splitlines()) == 1


def test_export_refuses_any_path_under_data_eval(
    tmp_db_path: Path, item: db.RoundItem, tmp_path: Path, capsys
) -> None:
    _rescored(tmp_db_path, item)
    target = tmp_path / "data" / "eval" / "eval_v9" / "x.jsonl"

    assert run(tmp_db_path, "export-dev", "--out", str(target)) == 2
    assert not target.exists() and not target.parent.exists()
    assert "data/eval" in capsys.readouterr().err
    with pytest.raises(tool.ReviewError):
        tool.refuse_eval_path(tool.EVAL_DIR / "eval_v1" / "x.jsonl")


def test_export_skips_texts_already_in_dev_or_eval(
    tmp_db_path: Path, tmp_path: Path, capsys
) -> None:
    items = items_of(tmp_db_path, "dups")
    in_dev, in_eval, fresh = items[0], items[1], items[2]
    for it in (in_dev, in_eval, fresh):
        dispute(tmp_db_path, it, ["doubt"] * 3, status="in_review", dispute_id=f"d-{it.item_id}")
        assert run(tmp_db_path, "rescore", f"d-{it.item_id}", "--technique", "no_technique") == 0
    dev_dir, eval_dir = tmp_path / "dev", tmp_path / "eval"
    dev_dir.mkdir()
    (eval_dir / "eval_vX").mkdir(parents=True)
    # Same text with different case and punctuation: still a duplicate.
    (dev_dir / "a.jsonl").write_text(
        json.dumps({"id": "a", "text": in_dev.text.upper() + "!!"}) + "\n", encoding="utf-8"
    )
    eval_text = "  " + in_eval.text.replace(" ", "   ") + " "
    (eval_dir / "eval_vX" / "b.jsonl").write_text(
        json.dumps({"id": "b", "text": eval_text}) + "\n", encoding="utf-8"
    )
    out = tmp_path / "out" / "confirmed.jsonl"

    report = tool.cmd_export_dev(tmp_db_path, out, dev_dir=dev_dir, eval_dir=eval_dir)

    assert (report.written, report.in_dev, report.in_eval) == (1, 1, 1)
    written = out.read_text(encoding="utf-8")
    assert fresh.text in written
    assert in_eval.text not in written and eval_text.strip() not in written
    assert report.notes == []
    capsys.readouterr()


def test_export_warns_when_there_is_no_eval_folder(
    tmp_db_path: Path, item: db.RoundItem, tmp_path: Path
) -> None:
    _rescored(tmp_db_path, item)

    report = tool.cmd_export_dev(
        tmp_db_path, tmp_path / "o.jsonl", dev_dir=tmp_path / "nodev", eval_dir=tmp_path / "noeval"
    )

    assert report.written == 1
    assert any("eval duplicate check did not run" in n for n in report.notes)


def test_script_wrapper_runs_the_same_tool(tmp_db_path: Path, capsys) -> None:
    spec = importlib.util.spec_from_file_location("scripts_disputes_review", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.main(["--db-path", str(tmp_db_path), "list"]) == 0
    assert "0 dispute(s)" in capsys.readouterr().out
