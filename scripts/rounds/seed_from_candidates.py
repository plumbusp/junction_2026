"""Label candidate events from the news job and store them as game rounds.

    uv run python scripts/rounds/seed_from_candidates.py data/news/candidates-2026-11-13.jsonl
    uv run python scripts/rounds/seed_from_candidates.py <file> --db-path /tmp/rounds.db

Reads one ``candidates-*.jsonl`` file written by ``scripts/news/run_daily.py``, labels
every headline with the one core (``classify``; stub or live per ``CORE_MODE``) and
inserts each event with ``app.db.insert_candidate_round``. That function drops headlines
with a disputed reference and keeps the event only if three headlines including a
``no_technique`` one remain. This is the only way rounds enter the database for now.

Live mode calls three models per headline and needs ``AI_ROUTER_API_KEY``; run it only
when live calls are approved. Headline text is not printed.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app import db
from app.config import get_settings, require_live_key
from app.core.classifier import classify
from app.core.types import Verdict

DRY_RUN_NOTE = "Fixture headlines from fictional test outlets, not real news."
LIVE_NOTE = (
    "Real headlines from RSS feeds. An outlet's declared leaning describes the outlet, "
    "not the headline."
)


def event_label(record: dict) -> str:
    """A neutral event label from the cluster's top terms (never from one headline)."""
    terms = [str(t) for t in record.get("top_terms", []) if str(t).strip()][:5]
    return "Keywords: " + ", ".join(terms) if terms else "One event, several outlets"


def read_candidates(path: Path) -> list[dict]:
    records = []
    with path.open(encoding="utf-8") as fh:
        for number, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{path.name}: line {number} is not valid JSON"
                ) from exc
    return records


def seed(
    records: Iterable[dict],
    db_path: Path,
    classify_fn: Callable[[str], Verdict] = classify,
) -> list[db.InsertResult]:
    """Label and insert every candidate record; returns one result per record."""
    results = []
    for record in records:
        headlines = [
            h for h in record.get("headlines", []) if str(h.get("title", "")).strip()
        ]
        if not headlines:
            continue
        items = [
            {
                "text": h["title"],
                "outlet": h.get("outlet_name") or h.get("outlet") or "",
                "link": h.get("link"),
                "published": h.get("published"),
                "declared_leaning": h.get("declared_leaning"),
            }
            for h in headlines
        ]
        references = [classify_fn(item["text"]) for item in items]
        note = DRY_RUN_NOTE if record.get("mode") == "dry_run" else LIVE_NOTE
        results.append(
            db.insert_candidate_round(
                db_path,
                event=event_label(record),
                items=items,
                references=references,
                cluster_id=record.get("cluster_id"),
                source_note=note,
            )
        )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "candidates", type=Path, help="a data/news/candidates-*.jsonl file"
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="SQLite path (default: DB_PATH setting)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s"
    )

    settings = get_settings()
    require_live_key(settings)
    db_path = args.db_path or settings.db_path
    records = read_candidates(args.candidates)
    results = seed(records, db_path)

    print(f"core mode: {settings.core_mode}")
    print(f"candidates: {len(records)} events in {args.candidates.name}")
    for result in results:
        state = "new" if result.created else "existing"
        extra = f" ({result.reason})" if result.reason else ""
        print(
            f"  {result.round_id}: {result.status}, {state}, "
            f"{result.n_in_play} of {result.n_items} headlines in play{extra}"
        )
    ready = sum(1 for r in results if r.status == db.STATUS_READY)
    print(f"ready rounds: {ready} of {len(results)}")
    print(f"database: {db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
