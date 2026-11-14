"""Review tool for player disputes: queued -> in review -> rescored | rejected.

    python -m app.tools.disputes list [--status in_review]
    python -m app.tools.disputes escalate <dispute_id>
    python -m app.tools.disputes rescore <dispute_id> --technique doubt [--spans "a|b"]
    python -m app.tools.disputes reject <dispute_id>
    python -m app.tools.disputes export-dev --out data/dev/disputes_confirmed.jsonl

Inside the container: ``docker exec <container> python -m app.tools.disputes list``.
``scripts/disputes/review.py`` is a thin wrapper around this module.

Honesty rules enforced here (docs/RULES.md section 3):

* Player disputes are opinions, not labels. Only a reviewer's ``rescore`` creates a label,
  and it can only go to the dev set (``check_target_set``).
* ``export-dev`` writes only the text of a real headline stored in ``round_items`` (never a
  player's comment), refuses any path under ``data/eval``, and skips every text that is
  already in a ``data/dev`` or ``data/eval`` file (normalized-text check). Eval files are
  read only to hash the normalized text for that check; nothing is copied from them.
  Rounds labelled by the stub core are fixtures, not real headlines, and are skipped
  unless ``--include-stub`` is given. The export is idempotent.
* ``list`` prints ids, counts and techniques only: no comment text, no headline text.

The ``data/eval`` folder is not part of the Docker image, so inside the container the
eval part of the duplicate check cannot run; the tool says so. Run ``export-dev`` on a
copy of the database next to the full repository to get the complete check.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

from app import db
from app.config import REPO_ROOT, get_settings
from app.game.disputes import (
    DEV_SET,
    Dispute,
    DisputeStatus,
    IllegalTransitionError,
    check_target_set,
    escalate_by_team,
    reject,
    rescore,
)

DEV_DIR = REPO_ROOT / "data" / "dev"
EVAL_DIR = REPO_ROOT / "data" / "eval"
DEFAULT_OUT = DEV_DIR / "disputes_confirmed.jsonl"
SOURCE = "dispute"

_WORD = re.compile(r"\w+", re.UNICODE)


class ReviewError(Exception):
    """A request the tool refuses; the message is safe to print (no text, no secrets)."""


# ---------- Normalized-text duplicate check ----------


def normalize(text: str) -> str:
    """Case-folded words only: punctuation, spacing and case do not hide a duplicate."""
    return " ".join(_WORD.findall(unicodedata.normalize("NFC", text).casefold()))


def text_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def hash_texts_in(directory: Path) -> tuple[set[str], int]:
    """Hashes of the normalized ``text`` of every ``*.jsonl`` under ``directory``.

    Only hashes are kept: the texts themselves are never stored or returned. Returns
    (hashes, number of files read); a missing directory gives (set(), 0).
    """
    hashes: set[str] = set()
    files = 0
    if not directory.is_dir():
        return hashes, files
    for path in sorted(directory.rglob("*.jsonl")):
        files += 1
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                text = record.get("text") if isinstance(record, dict) else None
                if isinstance(text, str) and text.strip():
                    hashes.add(text_hash(text))
    return hashes, files


def _under(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def refuse_eval_path(out: Path, eval_dir: Path = EVAL_DIR) -> Path:
    """Resolve ``out``; refuse anything under ``data/eval`` (the frozen test set)."""
    resolved = out.resolve()
    parts = [p.lower() for p in resolved.parts]
    in_named_eval = any(a == "data" and b == "eval" for a, b in pairwise(parts))
    if _under(resolved, eval_dir.resolve()) or in_named_eval:
        raise ReviewError(
            "refusing to write under data/eval: disputed items go to the dev set only"
        )
    return resolved


# ---------- Subcommands ----------


def _load(db_path: Path, dispute_id: str) -> db.DisputeRecord:
    record = db.get_dispute_by_id(db_path, dispute_id)
    if record is None:
        raise ReviewError(f"no dispute with id {dispute_id!r}")
    return record


def _state(record: db.DisputeRecord) -> Dispute:
    return Dispute(
        item_id=record.item_id,
        status=DisputeStatus(record.status),
        voters=record.voters,
        threshold=record.threshold,
    )


def _transition(
    db_path: Path,
    record: db.DisputeRecord,
    new_state: Dispute,
    *,
    technique: str | None = None,
    spans: Sequence[str] | None = None,
) -> None:
    moved = db.set_dispute_status(
        db_path,
        record.dispute_id,
        expected=record.status,
        status=str(new_state.status),
        new_technique=technique,
        new_evidence_spans=spans,
    )
    if not moved:
        raise ReviewError("the dispute changed in the meantime; run `list` and retry")


def cmd_list(db_path: Path, status: str | None) -> list[str]:
    if status is not None and status not in {s.value for s in DisputeStatus}:
        raise ReviewError(
            f"unknown status {status!r}; use one of "
            + ", ".join(s.value for s in DisputeStatus)
        )
    rows = db.list_dispute_summaries(db_path, status)
    lines = [
        f"{len(rows)} dispute(s)" + (f" with status {status}" if status else "")
    ]
    for row in rows:
        picks = ", ".join(f"{t} x{n}" for t, n in row.player_techniques.items())
        line = (
            f"{row.dispute_id}  {row.status:<9}  votes {row.votes}/{row.threshold}  "
            f"reference {row.reference_technique}  players: {picks or 'none'}  "
            f"core {row.core_mode}"
        )
        if row.new_technique:
            line += f"  rescored to {row.new_technique} ({len(row.new_evidence_spans)} span(s))"
        lines.append(line)
    return lines


def cmd_escalate(db_path: Path, dispute_id: str) -> str:
    record = _load(db_path, dispute_id)
    try:
        new_state = escalate_by_team(_state(record))
    except IllegalTransitionError as exc:
        raise ReviewError(str(exc)) from None
    _transition(db_path, record, new_state)
    return f"{dispute_id}: queued -> in_review (team decision)"


def parse_spans(raw: str | None) -> list[str]:
    return [s.strip() for s in (raw or "").split("|") if s.strip()]


def cmd_rescore(
    db_path: Path, dispute_id: str, technique: str, spans: list[str]
) -> str:
    record = _load(db_path, dispute_id)
    text = db.get_item_text(db_path, record.item_id) or ""
    missing = [s for s in spans if s not in text]
    if missing:
        raise ReviewError(
            f"{len(missing)} span(s) are not exact substrings of the headline; "
            "evidence spans must be copied from it"
        )
    if technique == "no_technique" and spans:
        raise ReviewError("no_technique has no evidence spans")
    try:
        new_state, target = rescore(_state(record), technique, spans)
    except (IllegalTransitionError, ValueError) as exc:
        raise ReviewError(str(exc)) from None
    check_target_set(target)  # a rescored item may only ever go to dev
    _transition(db_path, record, new_state, technique=technique, spans=spans)
    return (
        f"{dispute_id}: in_review -> rescored as {technique} "
        f"({len(spans)} span(s)); target set: {target}"
    )


def cmd_reject(db_path: Path, dispute_id: str) -> str:
    record = _load(db_path, dispute_id)
    try:
        new_state = reject(_state(record))
    except IllegalTransitionError as exc:
        raise ReviewError(str(exc)) from None
    _transition(db_path, record, new_state)
    return f"{dispute_id}: in_review -> rejected (reference answer unchanged)"


@dataclass
class ExportReport:
    out: Path
    candidates: int = 0  # rescored disputes considered
    written: int = 0
    already_exported: int = 0
    in_dev: int = 0
    in_eval: int = 0
    skipped_stub: int = 0
    dev_files: int = 0
    eval_files: int = 0
    notes: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        out = [
            f"rescored disputes considered: {self.candidates}",
            f"written: {self.written}",
            f"already exported: {self.already_exported}",
            f"skipped, text already in data/dev: {self.in_dev}",
            f"skipped, text already in data/eval: {self.in_eval}",
            f"skipped, stub-core round: {self.skipped_stub}",
            f"output: {self.out}",
        ]
        return out + self.notes


def _read_existing(out: Path) -> tuple[set[str], set[str]]:
    ids: set[str] = set()
    hashes: set[str] = set()
    if out.is_file():
        with out.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                record = json.loads(line)
                ids.add(str(record.get("id")))
                hashes.add(text_hash(str(record.get("text", ""))))
    return ids, hashes


def cmd_export_dev(
    db_path: Path,
    out: Path,
    *,
    include_stub: bool = False,
    dev_dir: Path = DEV_DIR,
    eval_dir: Path = EVAL_DIR,
) -> ExportReport:
    out = refuse_eval_path(out, eval_dir)
    check_target_set(DEV_SET)
    report = ExportReport(out=out)

    existing_ids, existing_hashes = _read_existing(out)
    dev_hashes, report.dev_files = hash_texts_in(dev_dir)
    # The output file may live in data/dev: its own records are "already exported".
    dev_hashes -= existing_hashes
    eval_hashes, report.eval_files = hash_texts_in(eval_dir)
    if report.eval_files == 0:
        report.notes.append(
            "WARNING: no data/eval files found here, so the eval duplicate check did not "
            "run (the folder is not in the Docker image). Re-run the export next to the "
            "full repository before using the file."
        )

    new_lines: list[str] = []
    seen = set(existing_hashes)
    for row in db.list_dispute_summaries(db_path, DisputeStatus.RESCORED.value):
        report.candidates += 1
        dev_id = f"dispute-{row.dispute_id}"
        text = db.get_item_text(db_path, row.item_id)
        if not text or not row.new_technique:
            continue
        if dev_id in existing_ids:
            report.already_exported += 1
            continue
        if row.core_mode == "stub" and not include_stub:
            report.skipped_stub += 1
            continue
        digest = text_hash(text)
        if digest in eval_hashes:
            report.in_eval += 1
            continue
        if digest in dev_hashes or digest in seen:
            report.in_dev += 1
            continue
        seen.add(digest)
        new_lines.append(
            json.dumps(
                {
                    "id": dev_id,
                    "source": SOURCE,
                    "text": text,
                    "technique": row.new_technique,
                    "evidence_spans": row.new_evidence_spans,
                },
                ensure_ascii=False,
            )
        )
        report.written += 1

    if new_lines:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(new_lines) + "\n")
    return report


# ---------- CLI ----------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="disputes",
        description="Review player disputes. Output never contains comments or headlines.",
    )
    parser.add_argument("--db-path", type=Path, default=None, help="SQLite path (default: DB_PATH)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="list disputes: ids, counts and techniques")
    p_list.add_argument("--status", default=None, help="queued, in_review, rescored or rejected")

    p_esc = sub.add_parser("escalate", help="team decision: queued -> in_review")
    p_esc.add_argument("dispute_id")

    p_res = sub.add_parser("rescore", help="record the reviewer's verdict (in_review -> rescored)")
    p_res.add_argument("dispute_id")
    p_res.add_argument("--technique", required=True)
    p_res.add_argument("--spans", default="", help='evidence spans separated by "|"')

    p_rej = sub.add_parser("reject", help="close the dispute, reference unchanged")
    p_rej.add_argument("dispute_id")

    p_exp = sub.add_parser("export-dev", help="write rescored items as dev examples (JSONL)")
    p_exp.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p_exp.add_argument(
        "--include-stub", action="store_true", help="also export rounds labelled by the stub core"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db_path = args.db_path or get_settings().db_path
    try:
        if args.command == "list":
            lines: Iterable[str] = cmd_list(db_path, args.status)
        elif args.command == "escalate":
            lines = [cmd_escalate(db_path, args.dispute_id)]
        elif args.command == "rescore":
            lines = [
                cmd_rescore(db_path, args.dispute_id, args.technique, parse_spans(args.spans))
            ]
        elif args.command == "reject":
            lines = [cmd_reject(db_path, args.dispute_id)]
        else:
            lines = cmd_export_dev(db_path, args.out, include_stub=args.include_stub).lines()
    except ReviewError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
