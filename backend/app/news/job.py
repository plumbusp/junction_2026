"""Daily news job: fetch (or load fixtures), cluster, write candidate events.

Output: ``data/news/candidates-YYYY-MM-DD.jsonl``, one candidate cluster per line
with its member headlines and outlets. Candidates are inputs to human labelling,
never labels. Database writes wait for ``app.db`` (owned by api-backend).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.config import REPO_ROOT
from app.news.cluster import (
    DEFAULT_MIN_OUTLETS,
    DEFAULT_THRESHOLD,
    DEFAULT_WINDOW_HOURS,
    Cluster,
    candidate_clusters,
    filter_window,
)
from app.news.feeds import FEEDS, LEANING_CAVEAT, Feed, check_unique
from app.news.rss import NewsItem, fetch_items, parse_feed

log = logging.getLogger(__name__)

DEFAULT_OUT_DIR = REPO_ROOT / "data" / "news"
FIXTURE_DIR = REPO_ROOT / "backend" / "tests" / "fixtures" / "rss"
FIXTURE_MANIFEST = "manifest.json"


@dataclass
class JobResult:
    path: Path
    mode: str
    run_time: datetime
    n_items: int
    n_in_window: int
    candidates: list[Cluster]
    failed_outlets: list[str] = field(default_factory=list)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def candidates_path(out_dir: Path, run_time: datetime) -> Path:
    return out_dir / f"candidates-{run_time.astimezone(UTC).date().isoformat()}.jsonl"


def cluster_to_record(
    cluster: Cluster,
    *,
    feeds_by_outlet: dict[str, Feed],
    mode: str,
    run_time: datetime,
    threshold: float,
    window_hours: int,
) -> dict:
    headlines = []
    for item in cluster.items:
        row = item.to_dict()
        feed = feeds_by_outlet.get(item.outlet)
        row["outlet_name"] = feed.name if feed else item.outlet
        headlines.append(row)
    return {
        "cluster_id": cluster.cluster_id,
        "mode": mode,
        "generated_at": _iso(run_time),
        "threshold": threshold,
        "window_hours": window_hours,
        "n_headlines": len(cluster.items),
        "n_outlets": cluster.n_outlets,
        "outlets": cluster.outlets,
        "first_published": _iso(cluster.first_published),
        "last_published": _iso(cluster.last_published),
        "cohesion": cluster.cohesion,
        "top_terms": cluster.top_terms,
        "labelled": False,
        "leaning_note": LEANING_CAVEAT,
        "headlines": headlines,
    }


def write_jsonl(records: Sequence[dict], path: Path) -> None:
    """Write records atomically (temp file, then replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    tmp.replace(path)


def run(
    items: Sequence[NewsItem],
    feeds: Sequence[Feed],
    *,
    now: datetime,
    out_dir: Path = DEFAULT_OUT_DIR,
    mode: str,
    threshold: float = DEFAULT_THRESHOLD,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    min_outlets: int = DEFAULT_MIN_OUTLETS,
    failed_outlets: Sequence[str] = (),
) -> JobResult:
    """Cluster ``items`` published in the window ending at ``now`` and write candidates."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    in_window = filter_window(items, now, window_hours)
    candidates = candidate_clusters(
        in_window,
        threshold=threshold,
        window_hours=window_hours,
        min_outlets=min_outlets,
    )
    feeds_by_outlet = {f.outlet: f for f in feeds}
    records = [
        cluster_to_record(
            c,
            feeds_by_outlet=feeds_by_outlet,
            mode=mode,
            run_time=now,
            threshold=threshold,
            window_hours=window_hours,
        )
        for c in candidates
    ]
    path = candidates_path(out_dir, now)
    write_jsonl(records, path)
    log.info("wrote %d candidate clusters to %s", len(records), path)
    return JobResult(
        path=path,
        mode=mode,
        run_time=now,
        n_items=len(items),
        n_in_window=len(in_window),
        candidates=candidates,
        failed_outlets=list(failed_outlets),
    )


def load_fixtures(
    fixture_dir: Path = FIXTURE_DIR,
) -> tuple[list[Feed], list[NewsItem], datetime]:
    """Read fixture feeds listed in ``manifest.json``. No network access."""
    manifest = json.loads((fixture_dir / FIXTURE_MANIFEST).read_text(encoding="utf-8"))
    now = datetime.fromisoformat(manifest["reference_time"])
    feeds: list[Feed] = []
    items: list[NewsItem] = []
    seen_links: set[str] = set()
    for entry in manifest["feeds"]:
        feed = Feed(
            outlet=entry["outlet"],
            name=entry["name"],
            url=entry["file"],
            leaning=entry["leaning"],
        )
        feeds.append(feed)
        body = (fixture_dir / entry["file"]).read_bytes()
        for item in parse_feed(body, feed):
            if item.link not in seen_links:
                seen_links.add(item.link)
                items.append(item)
    check_unique(feeds)
    return feeds, items, now


def run_dry(
    fixture_dir: Path = FIXTURE_DIR,
    out_dir: Path = DEFAULT_OUT_DIR,
    **params,
) -> JobResult:
    """Offline run on fixture feeds; ``now`` is the manifest's reference time."""
    feeds, items, now = load_fixtures(fixture_dir)
    return run(items, feeds, now=now, out_dir=out_dir, mode="dry_run", **params)


def run_live(
    out_dir: Path = DEFAULT_OUT_DIR,
    feeds: Sequence[Feed] = FEEDS,
    client: httpx.Client | None = None,
    now: datetime | None = None,
    **params,
) -> JobResult:
    """Fetch the configured feeds and write candidates. Touches the network."""
    report = fetch_items(feeds, client)
    return run(
        report.items,
        feeds,
        now=now or datetime.now(UTC),
        out_dir=out_dir,
        mode="live",
        failed_outlets=report.failed_outlets,
        **params,
    )
