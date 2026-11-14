"""Daily news job entry point.

    uv run python scripts/news/run_daily.py --dry-run     # fixtures only, no network
    uv run python scripts/news/run_daily.py               # live: fetches the configured feeds
    uv run python scripts/news/run_daily.py --live --seed # full pipeline once for today

Without --seed this writes data/news/candidates-YYYY-MM-DD.jsonl (inputs to human
labelling, not labels). With --live --seed it runs the whole daily pipeline once for
today's local date: fetch, cluster, label with the core in the current CORE_MODE, store
rounds and designate the daily challenge. That run is recorded in the job_runs table, so
it is a no-op if today already succeeded (add --force to run again). It prints counts
only, never headline text. Labelling in CORE_MODE=live calls the paid models.

The production image does not contain scripts/. There use the same pipeline as a module:

    docker exec spotthespin-app-1 python -m app.news.daily [--force]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.news import cluster, daily, job


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="read fixture feeds instead of the network",
    )
    parser.add_argument(
        "--fixtures-dir",
        type=Path,
        default=job.FIXTURE_DIR,
        help="fixture directory for --dry-run",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=job.DEFAULT_OUT_DIR,
        help="where candidates-*.jsonl is written",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=cluster.DEFAULT_THRESHOLD,
        help="average-linkage cosine threshold",
    )
    parser.add_argument(
        "--window-hours", type=int, default=cluster.DEFAULT_WINDOW_HOURS
    )
    parser.add_argument("--min-outlets", type=int, default=cluster.DEFAULT_MIN_OUTLETS)
    parser.add_argument(
        "--live",
        action="store_true",
        help="fetch live feeds (the default without --dry-run); needed with --seed",
    )
    parser.add_argument(
        "--seed",
        action="store_true",
        help="label, store rounds and pick the daily challenge (needs --live)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="with --seed: run even if today's run already succeeded",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="with --seed: SQLite path (default: DB_PATH setting)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.seed:
        if args.dry_run or not args.live:
            parser.error("--seed needs --live and cannot be combined with --dry-run")
        return daily.run_cli(args.db_path, force=args.force)
    if args.force or args.db_path:
        parser.error("--force and --db-path only apply with --live --seed")
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    params = {
        "threshold": args.threshold,
        "window_hours": args.window_hours,
        "min_outlets": args.min_outlets,
    }
    if args.dry_run:
        result = job.run_dry(args.fixtures_dir, args.out_dir, **params)
    else:
        result = job.run_live(args.out_dir, **params)

    print(f"mode: {result.mode}")
    print(f"reference time: {result.run_time.isoformat()}")
    print(
        f"items parsed: {result.n_items}, in {args.window_hours} h window: {result.n_in_window}"
    )
    if result.failed_outlets:
        print(f"failed feeds: {', '.join(result.failed_outlets)}")
    print(
        f"candidate clusters (>= {args.min_outlets} outlets): {len(result.candidates)}"
    )
    for cand in result.candidates:
        print(
            f"  [{cand.cluster_id}] {cand.n_outlets} outlets, cohesion {cand.cohesion:.3f}, terms: {', '.join(cand.top_terms)}"
        )
        for item in cand.items:
            print(f"    - {item.outlet}: {item.title}")
    print(f"written: {result.path}")
    print("database: not written (use --live --seed to store rounds)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
