"""SQLite storage: rounds, round_items, answers, disputes (+ dispute_votes).

Standard library only. Every function takes the database path, opens a short-lived
connection and closes it, so the module is safe to use from the API's worker threads,
the news job and scripts. The schema is created on first use of a path.

Rounds enter the database only through ``insert_candidate_round`` (called by
``scripts/rounds/seed_from_candidates.py`` and, later, the news job). Headlines whose
reference ``Verdict.disputed`` is True are stored but never put in play; the three
in-play headlines are chosen once, at insert time, by ``app.game.rounds.build_round``.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.core.stub import is_stub_verdict
from app.core.types import ModelVote, Verdict
from app.game.rounds import Headline, build_round

STATUS_READY = "ready"
STATUS_INELIGIBLE = "ineligible"

SCHEMA = """
CREATE TABLE IF NOT EXISTS rounds (
    round_id     TEXT PRIMARY KEY,
    cluster_id   TEXT,
    event        TEXT NOT NULL,
    event_date   TEXT NOT NULL,
    source_note  TEXT NOT NULL DEFAULT '',
    core_mode    TEXT NOT NULL CHECK (core_mode IN ('stub', 'live')),
    status       TEXT NOT NULL CHECK (status IN ('ready', 'ineligible')),
    created_at   TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS rounds_cluster_id
    ON rounds (cluster_id) WHERE cluster_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS round_items (
    item_id          TEXT PRIMARY KEY,
    round_id         TEXT NOT NULL REFERENCES rounds (round_id),
    text             TEXT NOT NULL,
    outlet           TEXT NOT NULL DEFAULT '',
    link             TEXT,
    published        TEXT,
    declared_leaning TEXT,
    reference        TEXT NOT NULL,
    disputed         INTEGER NOT NULL,
    position         INTEGER
);
CREATE INDEX IF NOT EXISTS round_items_round ON round_items (round_id);

CREATE TABLE IF NOT EXISTS answers (
    answer_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id        TEXT NOT NULL,
    round_id         TEXT NOT NULL REFERENCES rounds (round_id),
    item_id          TEXT NOT NULL REFERENCES round_items (item_id),
    technique        TEXT NOT NULL,
    selected_words   TEXT NOT NULL,
    technique_points INTEGER NOT NULL,
    evidence_points  INTEGER NOT NULL,
    total            INTEGER NOT NULL,
    created_at       TEXT NOT NULL,
    UNIQUE (player_id, round_id, item_id)
);

CREATE TABLE IF NOT EXISTS disputes (
    dispute_id         TEXT PRIMARY KEY,
    round_id           TEXT NOT NULL REFERENCES rounds (round_id),
    item_id            TEXT NOT NULL UNIQUE REFERENCES round_items (item_id),
    status             TEXT NOT NULL,
    threshold          INTEGER NOT NULL,
    new_technique      TEXT,
    new_evidence_spans TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dispute_votes (
    dispute_id          TEXT NOT NULL REFERENCES disputes (dispute_id),
    voter_id            TEXT NOT NULL,
    reference_technique TEXT,
    player_technique    TEXT NOT NULL,
    player_words        TEXT NOT NULL,
    comment             TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    PRIMARY KEY (dispute_id, voter_id)
);
"""

_initialized: set[str] = set()
_init_lock = threading.Lock()


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def init_db(db_path: Path | str) -> None:
    """Create the parent directory and the schema (idempotent)."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
    with _init_lock:
        _initialized.add(str(path.resolve()))


@contextmanager
def connect(db_path: Path | str) -> Iterator[sqlite3.Connection]:
    """Open a connection (schema ensured), commit on success, roll back on error, close."""
    path = Path(db_path)
    if str(path.resolve()) not in _initialized:
        init_db(path)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------- Verdict (de)serialization ----------


def verdict_to_dict(verdict: Verdict) -> dict:
    return asdict(verdict)


def verdict_from_dict(data: Mapping) -> Verdict:
    votes = [ModelVote(**vote) for vote in data.get("votes", [])]
    return Verdict(
        technique=data["technique"],
        evidence_spans=list(data.get("evidence_spans", [])),
        explanation=data.get("explanation", ""),
        agreement=int(data["agreement"]),
        n_models=int(data["n_models"]),
        disputed=bool(data["disputed"]),
        votes=votes,
    )


# ---------- Rounds ----------


@dataclass(frozen=True)
class InsertResult:
    round_id: str
    status: str  # "ready" or "ineligible"
    created: bool  # False when the cluster was already stored
    n_items: int
    n_in_play: int
    reason: str = ""


@dataclass(frozen=True)
class RoundItem:
    item_id: str
    round_id: str
    text: str
    outlet: str
    reference: Verdict
    position: int | None


@dataclass(frozen=True)
class StoredRound:
    round_id: str
    event: str
    event_date: str
    source_note: str
    core_mode: str
    status: str
    items: list[RoundItem]  # in-play items, ordered by position


def _event_date(items: Sequence[Mapping]) -> str:
    dates = sorted(
        str(item["published"])[:10] for item in items if item.get("published")
    )
    return dates[-1] if dates else datetime.now(UTC).date().isoformat()


def insert_candidate_round(
    db_path: Path | str,
    *,
    event: str,
    items: Sequence[Mapping],
    references: Sequence[Verdict],
    cluster_id: str | None = None,
    event_date: str | None = None,
    source_note: str = "",
) -> InsertResult:
    """Store one candidate event and pick its three in-play headlines.

    ``items``: dicts with ``text`` and optional ``outlet``, ``link``, ``published``,
    ``declared_leaning``. ``references``: one ``Verdict`` per item from ``classify``.
    Headlines with a disputed reference are stored but excluded before ``build_round``.
    The round is ``ready`` when ``build_round`` succeeds (three headlines, at least one
    ``no_technique``), else ``ineligible``. Re-inserting a known ``cluster_id`` is a
    no-op that returns the stored round. ``core_mode`` is derived from the verdicts.
    """
    if len(items) != len(references):
        raise ValueError("items and references must have the same length")
    if not items:
        raise ValueError("a candidate round needs at least one item")
    if not event or not event.strip():
        raise ValueError("event must be a non-empty string")
    for item in items:
        if not isinstance(item.get("text"), str) or not item["text"].strip():
            raise ValueError("every item needs a non-empty 'text'")

    with connect(db_path) as conn:
        if cluster_id:
            row = conn.execute(
                "SELECT round_id, status FROM rounds WHERE cluster_id = ?",
                (cluster_id,),
            ).fetchone()
            if row is not None:
                counts = conn.execute(
                    "SELECT COUNT(*), COUNT(position) FROM round_items WHERE round_id = ?",
                    (row["round_id"],),
                ).fetchone()
                return InsertResult(
                    round_id=row["round_id"],
                    status=row["status"],
                    created=False,
                    n_items=counts[0],
                    n_in_play=counts[1],
                    reason="cluster already stored",
                )

        round_id = f"r-{cluster_id}" if cluster_id else f"r-{uuid.uuid4().hex[:12]}"
        item_ids = [f"{round_id}-h{i + 1}" for i in range(len(items))]
        pool = [
            Headline(
                id=item_id,
                event_id=round_id,
                text=item["text"],
                reference=ref,
                source=str(item.get("outlet") or ""),
            )
            for item_id, item, ref in zip(item_ids, items, references, strict=True)
            if not ref.disputed
        ]
        positions: dict[str, int] = {}
        reason = ""
        try:
            chosen = build_round(pool, rng_seed=round_id)
            positions = {h.id: pos for pos, h in enumerate(chosen.headlines)}
        except ValueError:
            reason = "needs three undisputed headlines including a no_technique one"
        status = STATUS_READY if positions else STATUS_INELIGIBLE
        core_mode = "stub" if any(is_stub_verdict(r) for r in references) else "live"

        conn.execute(
            "INSERT INTO rounds (round_id, cluster_id, event, event_date, source_note,"
            " core_mode, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                round_id,
                cluster_id,
                event.strip(),
                event_date or _event_date(items),
                source_note,
                core_mode,
                status,
                _now(),
            ),
        )
        for item_id, item, ref in zip(item_ids, items, references, strict=True):
            conn.execute(
                "INSERT INTO round_items (item_id, round_id, text, outlet, link, published,"
                " declared_leaning, reference, disputed, position)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item_id,
                    round_id,
                    item["text"],
                    str(item.get("outlet") or ""),
                    item.get("link"),
                    item.get("published"),
                    item.get("declared_leaning"),
                    json.dumps(verdict_to_dict(ref), ensure_ascii=False),
                    int(ref.disputed),
                    positions.get(item_id),
                ),
            )
    return InsertResult(
        round_id=round_id,
        status=status,
        created=True,
        n_items=len(items),
        n_in_play=len(positions),
        reason=reason,
    )


def _row_to_item(row: sqlite3.Row) -> RoundItem:
    return RoundItem(
        item_id=row["item_id"],
        round_id=row["round_id"],
        text=row["text"],
        outlet=row["outlet"],
        reference=verdict_from_dict(json.loads(row["reference"])),
        position=row["position"],
    )


def list_ready_round_ids(db_path: Path | str, core_mode: str) -> list[str]:
    """Playable rounds whose references were produced in ``core_mode``, newest first."""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT round_id FROM rounds WHERE status = ? AND core_mode = ?"
            " ORDER BY event_date DESC, round_id",
            (STATUS_READY, core_mode),
        ).fetchall()
    return [row["round_id"] for row in rows]


def get_round(db_path: Path | str, round_id: str) -> StoredRound | None:
    """A stored round with its in-play items (position order), or None."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM rounds WHERE round_id = ?", (round_id,)
        ).fetchone()
        if row is None:
            return None
        items = conn.execute(
            "SELECT * FROM round_items WHERE round_id = ? AND position IS NOT NULL"
            " ORDER BY position",
            (round_id,),
        ).fetchall()
    return StoredRound(
        round_id=row["round_id"],
        event=row["event"],
        event_date=row["event_date"],
        source_note=row["source_note"],
        core_mode=row["core_mode"],
        status=row["status"],
        items=[_row_to_item(item) for item in items],
    )


def get_round_item(
    db_path: Path | str, round_id: str, item_id: str
) -> RoundItem | None:
    """An in-play item of a ready round, or None."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT i.* FROM round_items i JOIN rounds r ON r.round_id = i.round_id"
            " WHERE i.round_id = ? AND i.item_id = ? AND i.position IS NOT NULL"
            " AND r.status = ?",
            (round_id, item_id, STATUS_READY),
        ).fetchone()
    return _row_to_item(row) if row is not None else None


def count_in_play(db_path: Path | str, round_id: str) -> int:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM round_items WHERE round_id = ? AND position IS NOT NULL",
            (round_id,),
        ).fetchone()
    return int(row[0])


# ---------- Answers ----------


class DuplicateAnswerError(Exception):
    """The player already answered this item in this round."""


def insert_answer(
    db_path: Path | str,
    *,
    player_id: str,
    round_id: str,
    item_id: str,
    technique: str,
    selected_words: Sequence[str],
    technique_points: int,
    evidence_points: int,
    total: int,
) -> None:
    """Store one answer; raises ``DuplicateAnswerError`` on a second answer for the item."""
    try:
        with connect(db_path) as conn:
            conn.execute(
                "INSERT INTO answers (player_id, round_id, item_id, technique, selected_words,"
                " technique_points, evidence_points, total, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    player_id,
                    round_id,
                    item_id,
                    technique,
                    json.dumps(list(selected_words), ensure_ascii=False),
                    technique_points,
                    evidence_points,
                    total,
                    _now(),
                ),
            )
    except sqlite3.IntegrityError as exc:
        raise DuplicateAnswerError(item_id) from exc


def answer_totals(db_path: Path | str, player_id: str, round_id: str) -> list[int]:
    """Totals of the player's answers in the round, in answer order."""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT total FROM answers WHERE player_id = ? AND round_id = ? ORDER BY answer_id",
            (player_id, round_id),
        ).fetchall()
    return [int(row["total"]) for row in rows]


def completed_round_ids(db_path: Path | str, player_id: str) -> set[str]:
    """Rounds in which the player answered every in-play item."""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT a.round_id FROM answers a WHERE a.player_id = ? GROUP BY a.round_id"
            " HAVING COUNT(*) >= (SELECT COUNT(*) FROM round_items i"
            "  WHERE i.round_id = a.round_id AND i.position IS NOT NULL)",
            (player_id,),
        ).fetchall()
    return {row["round_id"] for row in rows}


# ---------- Disputes ----------


@dataclass(frozen=True)
class DisputeRecord:
    dispute_id: str
    round_id: str
    item_id: str
    status: str
    threshold: int
    voters: frozenset[str]


def get_dispute(db_path: Path | str, item_id: str) -> DisputeRecord | None:
    with connect(db_path) as conn:
        return _get_dispute(conn, item_id)


def _get_dispute(conn: sqlite3.Connection, item_id: str) -> DisputeRecord | None:
    row = conn.execute(
        "SELECT * FROM disputes WHERE item_id = ?", (item_id,)
    ).fetchone()
    if row is None:
        return None
    voters = conn.execute(
        "SELECT voter_id FROM dispute_votes WHERE dispute_id = ?", (row["dispute_id"],)
    ).fetchall()
    return DisputeRecord(
        dispute_id=row["dispute_id"],
        round_id=row["round_id"],
        item_id=row["item_id"],
        status=row["status"],
        threshold=int(row["threshold"]),
        voters=frozenset(v["voter_id"] for v in voters),
    )


def save_dispute_vote(
    db_path: Path | str,
    *,
    dispute_id: str,
    round_id: str,
    item_id: str,
    status: str,
    threshold: int,
    voter_id: str,
    reference_technique: str | None,
    player_technique: str,
    player_words: Sequence[str],
    comment: str,
) -> None:
    """Upsert the dispute row (status) and record the voter's first vote (later ones ignored)."""
    now = _now()
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO disputes (dispute_id, round_id, item_id, status, threshold,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (item_id) DO UPDATE SET status = excluded.status,"
            " updated_at = excluded.updated_at",
            (dispute_id, round_id, item_id, status, threshold, now, now),
        )
        conn.execute(
            "INSERT OR IGNORE INTO dispute_votes (dispute_id, voter_id, reference_technique,"
            " player_technique, player_words, comment, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                dispute_id,
                voter_id,
                reference_technique,
                player_technique,
                json.dumps(list(player_words), ensure_ascii=False),
                comment,
                now,
            ),
        )
