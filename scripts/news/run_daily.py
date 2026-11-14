"""Daily news job entry point.

    uv run python scripts/news/run_daily.py --dry-run   # fixtures only, no network
    uv run python scripts/news/run_daily.py             # live: fetches the configured feeds

Writes data/news/candidates-YYYY-MM-DD.jsonl. Candidates are inputs to human
labelling, not labels. Database writes are not implemented yet (app.db pending).
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

from app.news import cluster, job


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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
    print("DB integration: pending (app.db not available yet)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
