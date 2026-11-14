"""Daily news job: fetch (or load fixtures), cluster, write candidate events.

Two paths:

* ``run`` / ``run_dry`` / ``run_live``: write ``data/news/candidates-YYYY-MM-DD.jsonl``,
  one candidate cluster per line. Candidates are inputs to human labelling, never labels.
* ``run_live_rounds``: the daily challenge pipeline. Fetch live feeds, cluster, label the
  best clusters with the one core (``classify``) and store them as rounds via
  ``app.db.insert_candidate_round``, then designate the daily challenge. It writes no files
  (the production container has no writable data directory) and never logs headline text.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app import db
from app.config import REPO_ROOT, Settings, require_live_key
from app.core.cache import CACHE_DIR
from app.core.classifier import classify
from app.core.stub import is_stub_verdict
from app.core.types import Verdict
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


# ---------- Daily challenge pipeline (live feeds -> labelled rounds) ----------

LIVE_NOTE = (
    "Real headlines from RSS feeds. An outlet's declared leaning describes the outlet, "
    "not the headline."
)
MIN_LABELLED_PER_CLUSTER = 3  # a round needs three undisputed headlines


class StubVerdictError(RuntimeError):
    """A stub verdict reached the live pipeline. Real rounds must never carry one."""


class NoItemsError(RuntimeError):
    """No feed returned a usable item (network outage or every feed blocked)."""


class LabellingUnavailableError(RuntimeError):
    """No model answered for any cluster (provider outage): retry later."""


@dataclass
class LiveRoundsResult:
    n_items: int = 0
    n_in_window: int = 0
    failed_outlets: list[str] = field(default_factory=list)
    n_candidates: int = 0  # clusters with >= min_outlets outlets
    n_clusters_used: int = 0  # the top clusters considered (<= max_clusters)
    n_labelled: int = 0  # headlines sent to classify (the paid step)
    n_rounds_new: int = 0
    n_rounds_existing: int = 0
    n_ready: int = 0
    n_ineligible: int = 0
    n_unlabelled_clusters: int = 0  # skipped: no model answered, retried later
    daily_round_id: str | None = None
    ready_round_ids: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        """Numbers only, safe to print and to store."""
        return {
            "items": self.n_items,
            "in_window": self.n_in_window,
            "failed_feeds": len(self.failed_outlets),
            "candidates": self.n_candidates,
            "clusters_used": self.n_clusters_used,
            "labelled": self.n_labelled,
            "rounds_new": self.n_rounds_new,
            "rounds_existing": self.n_rounds_existing,
            "ready": self.n_ready,
            "ineligible": self.n_ineligible,
            "unlabelled_clusters": self.n_unlabelled_clusters,
            "daily": int(self.daily_round_id is not None),
        }


def event_label(top_terms: Sequence[str]) -> str:
    """A neutral event label from the cluster's top terms (never from one headline)."""
    terms = [str(t) for t in top_terms if str(t).strip()][:5]
    return "Keywords: " + ", ".join(terms) if terms else "One event, several outlets"


def rank_clusters(clusters: Sequence[Cluster]) -> list[Cluster]:
    """Most outlets first, then the freshest story, then id (deterministic)."""
    return sorted(
        clusters,
        key=lambda c: (-c.n_outlets, -c.last_published.timestamp(), c.cluster_id),
    )


def select_headlines(cluster: Cluster, limit: int) -> list[NewsItem]:
    """Up to ``limit`` headlines: the newest one of each outlet first, then the rest."""
    ordered = sorted(cluster.items, key=lambda i: (-i.published.timestamp(), i.link))
    seen: set[str] = set()
    first: list[NewsItem] = []
    rest: list[NewsItem] = []
    for item in ordered:
        (rest if item.outlet in seen else first).append(item)
        seen.add(item.outlet)
    return (first + rest)[:limit]


def _check_cache_writable() -> None:
    """Fail before spending anything if verdicts could not be cached afterwards."""
    probe = CACHE_DIR / f".write-test-{uuid.uuid4().hex}"
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise RuntimeError("verdict cache directory is not writable") from exc


def run_live_rounds(
    db_path: Path | str,
    settings: Settings,
    *,
    local_date: str,
    feeds: Sequence[Feed] = FEEDS,
    client: httpx.Client | None = None,
    now: datetime | None = None,
    classify_fn: Callable[[str], Verdict] | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    min_outlets: int = DEFAULT_MIN_OUTLETS,
) -> LiveRoundsResult:
    """Fetch live feeds, label the best clusters and store them as rounds.

    Labelling is the only paid step, so it is capped: at most
    ``settings.daily_max_clusters`` clusters and ``settings.daily_max_headlines``
    headlines in total (the budget is shared fairly between the clusters). Clusters
    already stored are not labelled again. ``classify_fn`` defaults to the one core; in
    live mode a stub verdict raises ``StubVerdictError`` and, with the default core, a
    missing key or an unwritable verdict cache fails before any model is called. Finally
    one ready round of this run is designated the daily challenge for ``local_date``.
    """
    live = settings.core_mode == "live"
    if classify_fn is None:
        if live:
            require_live_key(settings)
            _check_cache_writable()
        classify_fn = classify
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    report = fetch_items(feeds, client)
    result = LiveRoundsResult(
        n_items=len(report.items), failed_outlets=list(report.failed_outlets)
    )
    if not report.items:
        raise NoItemsError("no feed returned any item")
    in_window = filter_window(report.items, now, window_hours)
    result.n_in_window = len(in_window)
    candidates = candidate_clusters(
        in_window,
        threshold=threshold,
        window_hours=window_hours,
        min_outlets=min_outlets,
    )
    result.n_candidates = len(candidates)
    top = rank_clusters(candidates)[: settings.daily_max_clusters]
    result.n_clusters_used = len(top)

    feed_names = {f.outlet: f.name for f in feeds}
    pool: list[str] = []
    pending: list[Cluster] = []
    for cluster in top:
        stored = db.get_round_by_cluster(db_path, cluster.cluster_id)
        if stored is None:
            pending.append(cluster)
            continue
        result.n_rounds_existing += 1
        if stored.status == db.STATUS_READY:
            pool.append(stored.round_id)

    remaining = settings.daily_max_headlines
    for index, cluster in enumerate(pending):
        if remaining < MIN_LABELLED_PER_CLUSTER:
            break
        share = remaining // (len(pending) - index)
        limit = min(len(cluster.items), max(MIN_LABELLED_PER_CLUSTER, share), remaining)
        chosen = select_headlines(cluster, limit)
        verdicts: list[Verdict] = []
        for item in chosen:
            verdict = classify_fn(item.title)
            result.n_labelled += 1
            remaining -= 1
            if live and is_stub_verdict(verdict):
                raise StubVerdictError("stub verdict in live mode")
            verdicts.append(verdict)
        if any(not v.votes for v in verdicts):
            # No model answered for some headline: do not freeze a degraded round.
            result.n_unlabelled_clusters += 1
            log.warning("cluster %s: no model verdict, skipped", cluster.cluster_id)
            continue
        inserted = db.insert_candidate_round(
            db_path,
            event=event_label(cluster.top_terms),
            items=[
                {
                    "text": item.title,
                    "outlet": feed_names.get(item.outlet, item.outlet),
                    "link": item.link,
                    "published": item.published.astimezone(UTC)
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "declared_leaning": item.declared_leaning,
                }
                for item in chosen
            ],
            references=verdicts,
            cluster_id=cluster.cluster_id,
            source_note=LIVE_NOTE,
            n_outlets=cluster.n_outlets,
        )
        if inserted.created:
            result.n_rounds_new += 1
        else:
            result.n_rounds_existing += 1
        if inserted.status == db.STATUS_READY:
            pool.append(inserted.round_id)

    result.n_ready = len(pool)
    result.n_ineligible = result.n_rounds_new + result.n_rounds_existing - len(pool)
    result.ready_round_ids = pool
    result.daily_round_id = db.pick_daily_challenge(
        db_path, local_date, pool, settings.core_mode
    )
    if result.n_unlabelled_clusters and not (
        result.n_rounds_new or result.n_rounds_existing
    ):
        raise LabellingUnavailableError("no cluster could be labelled")
    log.info("daily rounds: %s", result.counts())
    return result
