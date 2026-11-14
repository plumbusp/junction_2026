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
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.stub import is_stub_verdict
from app.core.types import ModelVote, Verdict
from app.game.rounds import Headline, build_round

STATUS_READY = "ready"
STATUS_INELIGIBLE = "ineligible"
NOVELTY_WINDOW = 7  # previous daily challenges compared when ranking by novelty

SCHEMA = """
CREATE TABLE IF NOT EXISTS rounds (
    round_id     TEXT PRIMARY KEY,
    cluster_id   TEXT,
    event        TEXT NOT NULL,
    event_date   TEXT NOT NULL,
    source_note  TEXT NOT NULL DEFAULT '',
    core_mode    TEXT NOT NULL CHECK (core_mode IN ('stub', 'live')),
    status       TEXT NOT NULL CHECK (status IN ('ready', 'ineligible')),
    created_at   TEXT NOT NULL,
    n_outlets    INTEGER,
    diversity    INTEGER,
    techniques   TEXT
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

-- One round per local date (in DAILY_TZ) is "the daily challenge".
CREATE TABLE IF NOT EXISTS daily_challenge (
    local_date TEXT PRIMARY KEY,
    round_id   TEXT NOT NULL UNIQUE REFERENCES rounds (round_id),
    chosen_at  TEXT NOT NULL
);

-- One row per local date: makes the daily job idempotent across restarts.
CREATE TABLE IF NOT EXISTS job_runs (
    run_date    TEXT PRIMARY KEY,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL CHECK (status IN ('running', 'ok', 'failed')),
    attempts    INTEGER NOT NULL DEFAULT 1,
    counts      TEXT NOT NULL DEFAULT '{}',
    error       TEXT NOT NULL DEFAULT ''
);
"""

_initialized: set[str] = set()
_init_lock = threading.Lock()


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _iso(moment: datetime) -> str:
    return (
        moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring a database created by an older version up to date (additive only)."""
    for name, decl in (
        ("n_outlets", "INTEGER"),
        ("diversity", "INTEGER"),
        ("techniques", "TEXT"),
    ):
        if name in {row[1] for row in conn.execute("PRAGMA table_info(rounds)")}:
            continue
        try:
            conn.execute(f"ALTER TABLE rounds ADD COLUMN {name} {decl}")
        except sqlite3.OperationalError:
            # A second process (the CLI next to the server) added it first.
            if name not in {
                row[1] for row in conn.execute("PRAGMA table_info(rounds)")
            }:
                raise
    _backfill_techniques(conn)


def _backfill_techniques(conn: sqlite3.Connection) -> None:
    """Derive ``diversity``/``techniques`` of ready rounds stored before those columns existed.

    Computed from the stored in-play references only: nothing is relabelled or invented.
    """
    rows = conn.execute(
        "SELECT round_id FROM rounds WHERE status = ? AND techniques IS NULL",
        (STATUS_READY,),
    ).fetchall()
    for row in rows:
        refs = conn.execute(
            "SELECT reference FROM round_items WHERE round_id = ? AND position IS NOT NULL",
            (row[0],),
        ).fetchall()
        techniques = sorted({json.loads(ref[0])["technique"] for ref in refs})
        if techniques:
            conn.execute(
                "UPDATE rounds SET diversity = ?, techniques = ? WHERE round_id = ?",
                (len(techniques), json.dumps(techniques), row[0]),
            )


def init_db(db_path: Path | str) -> None:
    """Create the parent directory and the schema, migrating older databases (idempotent)."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        _migrate(conn)
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
    diversity: int = 0  # distinct reference techniques among the in-play items (0: none)


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
    n_outlets: int | None = None,
) -> InsertResult:
    """Store one candidate event and pick its three in-play headlines.

    ``items``: dicts with ``text`` and optional ``outlet``, ``link``, ``published``,
    ``declared_leaning``. ``references``: one ``Verdict`` per item from ``classify``.
    Headlines with a disputed reference are stored but excluded before ``build_round``,
    which picks the three with the most distinct reference techniques (and a
    ``no_technique`` one when the event has one). The round is ``ready`` when
    ``build_round`` succeeds, else ``ineligible``; a ready round records ``diversity``
    (1-3) and the sorted ``techniques`` list (internal, never served to players).
    Re-inserting a known ``cluster_id`` is a
    no-op that returns the stored round. ``core_mode`` is derived from the verdicts.
    ``n_outlets`` is the number of distinct outlets of the whole event (it can exceed the
    stored items when the news job labels only a sample); default: distinct outlets in
    ``items``. The daily challenge prefers the round with the most outlets.
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
        techniques: list[str] = []
        reason = ""
        try:
            chosen = build_round(pool, rng_seed=round_id)
            positions = {h.id: pos for pos, h in enumerate(chosen.headlines)}
            techniques = list(chosen.techniques)
        except ValueError:
            reason = (
                "needs three undisputed headlines including a no_technique one "
                "or three distinct techniques"
            )
        status = STATUS_READY if positions else STATUS_INELIGIBLE
        core_mode = "stub" if any(is_stub_verdict(r) for r in references) else "live"
        if n_outlets is None:
            n_outlets = len({str(i.get("outlet") or "") for i in items} - {""})

        conn.execute(
            "INSERT INTO rounds (round_id, cluster_id, event, event_date, source_note,"
            " core_mode, status, created_at, n_outlets, diversity, techniques)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                round_id,
                cluster_id,
                event.strip(),
                event_date or _event_date(items),
                source_note,
                core_mode,
                status,
                _now(),
                n_outlets,
                len(techniques) if techniques else None,
                json.dumps(techniques) if techniques else None,
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
        diversity=len(techniques),
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


@dataclass(frozen=True)
class ClusterRound:
    round_id: str
    status: str
    core_mode: str


def get_round_by_cluster(db_path: Path | str, cluster_id: str) -> ClusterRound | None:
    """The round already stored for a news cluster, or None (saves labelling it twice)."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT round_id, status, core_mode FROM rounds WHERE cluster_id = ?",
            (cluster_id,),
        ).fetchone()
    if row is None:
        return None
    return ClusterRound(row["round_id"], row["status"], row["core_mode"])


# ---------- Daily challenge ----------


def get_daily_challenge(db_path: Path | str, local_date: str) -> str | None:
    """Round id designated as the daily challenge for ``local_date`` (YYYY-MM-DD), or None."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT round_id FROM daily_challenge WHERE local_date = ?", (local_date,)
        ).fetchone()
    return row["round_id"] if row else None


def _recent_daily_techniques(conn: sqlite3.Connection, local_date: str) -> set[str]:
    recent = conn.execute(
        "SELECT r.techniques FROM daily_challenge d JOIN rounds r"
        " ON r.round_id = d.round_id WHERE d.local_date < ?"
        " ORDER BY d.local_date DESC LIMIT ?",
        (local_date, NOVELTY_WINDOW),
    ).fetchall()
    used: set[str] = set()
    for entry in recent:
        used.update(json.loads(entry["techniques"] or "[]"))
    return used


def recent_daily_techniques(db_path: Path | str, local_date: str) -> frozenset[str]:
    """Techniques of the ``NOVELTY_WINDOW`` daily challenges before ``local_date``."""
    with connect(db_path) as conn:
        return frozenset(_recent_daily_techniques(conn, local_date))


def pick_daily_challenge(
    db_path: Path | str,
    local_date: str,
    round_ids: Sequence[str],
    core_mode: str,
) -> str | None:
    """Designate the daily challenge for ``local_date`` among ``round_ids``.

    An existing designation for the date is kept and returned, never replaced. Otherwise
    the choice is the best ready round in ``core_mode`` that has not been a daily challenge
    before, ranked by: (a) diversity, the number of distinct reference techniques, higher
    first; (b) novelty, the number of the round's techniques not used by the previous
    ``NOVELTY_WINDOW`` daily challenges, higher first; (c) distinct outlets of the event,
    more first; (d) the freshest headline; then newest insert and id (deterministic).
    The day is never left empty because diversity is low: the most diverse round wins even
    below 3 (see ``get_round_diversity``). Returns None only when no round qualifies.
    """
    with connect(db_path) as conn:
        existing = conn.execute(
            "SELECT round_id FROM daily_challenge WHERE local_date = ?", (local_date,)
        ).fetchone()
        if existing is not None:
            return existing["round_id"]
        ids = list(dict.fromkeys(round_ids))
        if not ids:
            return None
        marks = ",".join("?" * len(ids))
        rows = conn.execute(
            "SELECT r.round_id, r.diversity, r.techniques, r.created_at,"
            " COALESCE(r.n_outlets, (SELECT COUNT(DISTINCT i.outlet) FROM round_items i"
            "   WHERE i.round_id = r.round_id AND i.outlet != '')) AS outlets,"
            " COALESCE((SELECT MAX(i.published) FROM round_items i"
            "   WHERE i.round_id = r.round_id), '') AS freshest"
            f" FROM rounds r WHERE r.round_id IN ({marks})"
            " AND r.status = ? AND r.core_mode = ?"
            " AND r.round_id NOT IN (SELECT round_id FROM daily_challenge)",
            (*ids, STATUS_READY, core_mode),
        ).fetchall()
        if not rows:
            return None
        used = _recent_daily_techniques(conn, local_date)

        def novelty(row: sqlite3.Row) -> int:
            return len(set(json.loads(row["techniques"] or "[]")) - used)

        # Stable sorts from the least to the most significant key.
        ranked = sorted(rows, key=lambda r: r["round_id"])
        ranked.sort(key=lambda r: r["created_at"], reverse=True)
        ranked.sort(key=lambda r: r["freshest"], reverse=True)
        ranked.sort(key=lambda r: r["outlets"] or 0, reverse=True)
        ranked.sort(key=novelty, reverse=True)
        ranked.sort(key=lambda r: r["diversity"] or 0, reverse=True)
        chosen = ranked[0]["round_id"]
        conn.execute(
            "INSERT INTO daily_challenge (local_date, round_id, chosen_at)"
            " VALUES (?, ?, ?)",
            (local_date, chosen, _now()),
        )
    return chosen


def get_round_diversity(db_path: Path | str, round_id: str) -> int | None:
    """Distinct reference techniques among a round's in-play items (1-3), or None."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT diversity FROM rounds WHERE round_id = ?", (round_id,)
        ).fetchone()
    return int(row["diversity"]) if row and row["diversity"] is not None else None


@dataclass(frozen=True)
class DayReset:
    deleted: int
    kept: int
    kept_round_ids: frozenset[str]


def reset_day(
    db_path: Path | str,
    local_date: str,
    *,
    created_from: datetime,
    created_to: datetime,
    core_mode: str,
) -> DayReset:
    """Undo one day's rounds so the daily job can rebuild them (``--rebuild-today``).

    Deletes the ``local_date`` designation (when its round was labelled in ``core_mode``)
    and every round in ``core_mode`` created in ``[created_from, created_to)`` that has no
    recorded answers or disputes and is not another date's daily challenge. Rounds with
    answers or disputes are kept untouched and returned so the caller can avoid re-picking
    them. Rounds of the other core mode are never touched. The ``job_runs`` row of the
    date is kept (the caller holds its claim) but its attempt counter restarts at 1.
    """
    lo, hi = _iso(created_from), _iso(created_to)
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "DELETE FROM daily_challenge WHERE local_date = ? AND round_id IN"
            " (SELECT round_id FROM rounds WHERE core_mode = ?)",
            (local_date, core_mode),
        )
        today = conn.execute(
            "SELECT round_id FROM rounds WHERE core_mode = ? AND created_at >= ?"
            " AND created_at < ?",
            (core_mode, lo, hi),
        ).fetchall()
        deleted = 0
        kept: set[str] = set()
        for row in today:
            round_id = row["round_id"]
            in_use = conn.execute(
                "SELECT EXISTS (SELECT 1 FROM answers WHERE round_id = ?)"
                " OR EXISTS (SELECT 1 FROM disputes WHERE round_id = ?)"
                " OR EXISTS (SELECT 1 FROM daily_challenge WHERE round_id = ?)",
                (round_id, round_id, round_id),
            ).fetchone()[0]
            if in_use:
                kept.add(round_id)
                continue
            conn.execute("DELETE FROM round_items WHERE round_id = ?", (round_id,))
            conn.execute("DELETE FROM rounds WHERE round_id = ?", (round_id,))
            deleted += 1
        conn.execute(
            "UPDATE job_runs SET attempts = 1 WHERE run_date = ?", (local_date,)
        )
    return DayReset(deleted, len(kept), frozenset(kept))


# ---------- Job runs (idempotency of the daily job) ----------

JOB_RUNNING = "running"
JOB_OK = "ok"
JOB_FAILED = "failed"


@dataclass(frozen=True)
class JobClaim:
    claimed: bool
    reason: str  # "claimed", "done", "running", "retry_later" or "exhausted"
    attempts: int = 0
    retry_at: datetime | None = None


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)  # Python 3.11+ accepts the "Z" suffix


def claim_job_run(
    db_path: Path | str,
    run_date: str,
    *,
    now: datetime,
    force: bool = False,
    max_attempts: int = 3,
    retry_after_seconds: int = 3600,
    stale_after_seconds: int = 2 * 3600,
) -> JobClaim:
    """Atomically start the job for ``run_date`` unless it must not run.

    No row: claimed. ``ok``: "done" (never runs twice for a date). ``running`` and not
    stale: "running" (another process, for example the CLI next to the server). ``failed``:
    "exhausted" after ``max_attempts``, "retry_later" until ``retry_after_seconds`` have
    passed since the last attempt, otherwise claimed again. A ``running`` row older than
    ``stale_after_seconds`` is a crashed run and is claimed again. ``force`` skips the
    "done" and attempt checks but never steals a live ``running`` row.
    """
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM job_runs WHERE run_date = ?", (run_date,)
        ).fetchone()
        attempts = 1
        if row is not None:
            attempts = int(row["attempts"]) + 1
            if row["status"] == JOB_RUNNING:
                started = _parse_iso(row["started_at"])
                if (now - started).total_seconds() < stale_after_seconds:
                    return JobClaim(False, "running", int(row["attempts"]))
            elif not force:
                if row["status"] == JOB_OK:
                    return JobClaim(False, "done", int(row["attempts"]))
                if int(row["attempts"]) >= max_attempts:
                    return JobClaim(False, "exhausted", int(row["attempts"]))
                retry_at = _parse_iso(row["finished_at"] or row["started_at"])
                retry_at += timedelta(seconds=retry_after_seconds)
                if now < retry_at:
                    return JobClaim(
                        False, "retry_later", int(row["attempts"]), retry_at
                    )
        conn.execute(
            "INSERT INTO job_runs (run_date, started_at, status, attempts)"
            " VALUES (?, ?, ?, ?) ON CONFLICT (run_date) DO UPDATE SET"
            " started_at = excluded.started_at, finished_at = NULL,"
            " status = excluded.status, attempts = excluded.attempts, error = ''",
            (run_date, _iso(now), JOB_RUNNING, attempts),
        )
    return JobClaim(True, "claimed", attempts)


def finish_job_run(
    db_path: Path | str,
    run_date: str,
    *,
    status: str,
    now: datetime,
    counts: Mapping[str, object] | None = None,
    error: str = "",
) -> None:
    """Record the end of a claimed run. ``error`` is an error class name, never a message."""
    if status not in (JOB_OK, JOB_FAILED):
        raise ValueError("status must be 'ok' or 'failed'")
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE job_runs SET finished_at = ?, status = ?, counts = ?, error = ?"
            " WHERE run_date = ?",
            (_iso(now), status, json.dumps(dict(counts or {})), error, run_date),
        )


def get_job_run(db_path: Path | str, run_date: str) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM job_runs WHERE run_date = ?", (run_date,)
        ).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["counts"] = json.loads(data["counts"])
    return data


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
