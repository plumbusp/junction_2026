"""Daily news job: fetch (or load fixtures), cluster, write candidate events.

Two paths:

* ``run`` / ``run_dry`` / ``run_live``: write ``data/news/candidates-YYYY-MM-DD.jsonl``,
  one candidate cluster per line. Candidates are inputs to human labelling, never labels.
* ``run_live_rounds``: the daily challenge pipeline. Fetch live feeds, cluster, label the
  best clusters with the one core (``classify``) and store them as rounds via
  ``app.db.insert_candidate_round``, then designate the daily challenge. It writes no files
  (the production container has no writable data directory) and never logs headline text.
  Rounds are about one event (three headlines of the same story from different outlets).
  The mixed "lineup" round (three different stories) is built only when
  ``DAILY_LINEUP_POOL`` is above 0; it is off by default.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import defaultdict
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import httpx

from app import db
from app.config import REPO_ROOT, Settings, require_live_key
from app.core.cache import CACHE_DIR
from app.core.classifier import classify
from app.core.clients import ModelCallError, RouterClient
from app.core.stub import is_stub_verdict
from app.core.types import Verdict
from app.game.rounds import LineupHeadline, build_lineup
from app.news.cluster import (
    DEFAULT_MIN_OUTLETS,
    DEFAULT_THRESHOLD,
    DEFAULT_WINDOW_HOURS,
    Cluster,
    candidate_clusters,
    cluster_items,
    filter_window,
)
from app.news.feeds import FEEDS, LEANING_CAVEAT, Feed, check_unique
from app.news.rss import NewsItem, fetch_items, parse_feed
from app.scheduler import settings_tz

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

LINEUP_EVENT = "Today's lineup: three different stories"
_LINEUP_NOTE_TAIL = (
    " They are real RSS headlines, not written or edited by us. An outlet's declared "
    "leaning describes the outlet, not the headline."
)
LINEUP_NOTE = (
    "The three headlines come from three different stories and three different "
    "outlets." + _LINEUP_NOTE_TAIL
)
# Used only when the pool had no three headlines from three distinct stories.
LINEUP_NOTE_SHARED_STORY = (
    "The three headlines come from three different outlets; two of them may be about "
    "the same story." + _LINEUP_NOTE_TAIL
)


def lineup_cluster_id(local_date: str) -> str:
    """The stored id of the lineup round of a local date (one lineup per date)."""
    return f"lineup-{local_date}"


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
    n_rounds_deleted: int = 0  # --rebuild-today: today's unanswered rounds removed
    n_rounds_kept: int = 0  # --rebuild-today: today's rounds with answers/disputes
    n_pool_labelled: int = 0  # lineup pool headlines that received a model verdict
    n_pool_failed: int = 0  # lineup pool headlines skipped (error or no model answered)
    lineup_round_id: str | None = None
    lineup_diversity: int | None = None  # distinct techniques in the lineup (None: no lineup)
    daily_round_id: str | None = None
    daily_diversity: int | None = None  # distinct techniques in the daily (None: no daily)
    # The daily has a reference technique other than no_technique (None: no daily). False
    # means no ready round of the day had one: nothing is relabelled to force it.
    daily_has_technique: bool | None = None
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
            "rounds_deleted": self.n_rounds_deleted,
            "rounds_kept": self.n_rounds_kept,
            "pool_labelled": self.n_pool_labelled,
            # 0 when there is no lineup round today.
            "lineup_diversity": self.lineup_diversity or 0,
            "daily": int(self.daily_round_id is not None),
            # 0 when there is no daily; below 3 means the day's pool lacked variety.
            "daily_diversity": self.daily_diversity or 0,
            # 0 when there is no daily or the daily shows only clean headlines.
            "daily_has_technique": int(bool(self.daily_has_technique)),
        }


def event_label(top_terms: Sequence[str]) -> str:
    """A neutral event label from the cluster's top terms (never from one headline)."""
    terms = [str(t) for t in top_terms if str(t).strip()][:5]
    return "Keywords: " + ", ".join(terms) if terms else "One event, several outlets"


EVENT_TITLE_PROMPT = (
    "Below are news headlines from different outlets about one event. Write a neutral, "
    "factual label for the event in 3 to 9 words: no opinion, no emotional words, no "
    "quotes, no final period. Answer with the label only.\n\n"
)
EVENT_TITLE_MAX_CHARS = 80


def event_title(
    headlines: Sequence[str],
    top_terms: Sequence[str],
    settings: Settings,
    client: RouterClient | None = None,
) -> str:
    """A neutral event label written by one core model from all the cluster's headlines.

    Falls back to ``event_label`` (keywords) outside live mode, on any model error, or when
    the answer does not look like a short label.
    """
    fallback = event_label(top_terms)
    if settings.core_mode != "live" or not settings.core_models or not headlines:
        return fallback
    messages = [{"role": "user", "content": EVENT_TITLE_PROMPT + "\n".join(f"- {h}" for h in headlines)}]
    try:
        if client is not None:
            raw = client.complete(settings.core_models[0], messages)
        else:
            with RouterClient(settings.ai_router_base_url, settings.ai_router_api_key) as own:
                raw = own.complete(settings.core_models[0], messages)
    except (ModelCallError, httpx.HTTPError) as exc:
        log.warning("event title: %s, using keywords", exc)
        return fallback
    label = " ".join(str(raw).split()).strip().strip("\"'“”‘’").rstrip(".")
    if not label or len(label) > EVENT_TITLE_MAX_CHARS or not 2 <= len(label.split()) <= 12:
        return fallback
    return label


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


def select_pool(
    items: Sequence[NewsItem], limit: int, exclude_links: Collection[str] = ()
) -> list[NewsItem]:
    """Up to ``limit`` headlines balanced across outlets, newest first within each outlet.

    Round-robin: the newest headline of every outlet, then the second newest of every
    outlet, and so on. Links in ``exclude_links`` (already chosen for clusters) and repeated
    links are skipped. Deterministic: the order of ``items`` does not matter.
    """
    if limit <= 0:
        return []
    seen: set[str] = set(exclude_links)
    by_outlet: dict[str, list[NewsItem]] = defaultdict(list)
    for item in sorted(items, key=lambda i: (-i.published.timestamp(), i.link)):
        if item.link in seen:
            continue
        seen.add(item.link)
        by_outlet[item.outlet].append(item)
    chosen: list[NewsItem] = []
    depth = 0
    while len(chosen) < limit:
        layer = [queue[depth] for queue in by_outlet.values() if len(queue) > depth]
        if not layer:
            break
        layer.sort(key=lambda i: (-i.published.timestamp(), i.link))
        chosen.extend(layer[: limit - len(chosen)])
        depth += 1
    return chosen


def _lineup_headline(
    item: NewsItem, verdict: Verdict, story_id: str, index: int
) -> LineupHeadline:
    return LineupHeadline(
        id=f"h{index}",
        story_id=story_id,
        outlet=item.outlet,
        text=item.title,
        reference=verdict,
        leaning=item.declared_leaning,
        published=item.published.timestamp(),
        link=item.link,
    )


def _local_day_bounds(settings: Settings, local_date: str) -> tuple[datetime, datetime]:
    """Start (inclusive) and end (exclusive) of the local day ``local_date`` as aware datetimes."""
    tz = settings_tz(settings)
    day = date.fromisoformat(local_date)
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
    return start, end


def _check_cache_writable() -> None:
    """Fail before spending anything if verdicts could not be cached afterwards."""
    probe = CACHE_DIR / f".write-test-{uuid.uuid4().hex}"
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise RuntimeError("verdict cache directory is not writable") from exc


def _lineup_round(
    db_path: Path | str,
    settings: Settings,
    result: LiveRoundsResult,
    *,
    local_date: str,
    in_window: Sequence[NewsItem],
    story_of: dict[str, str],
    feed_names: dict[str, str],
    classify_fn: Callable[[str], Verdict],
    live: bool,
    remaining: int,
    cluster_links: set[str],
    lineup_pool: list[LineupHeadline],
    ready: list[str],
    fallback: list[str],
    kept: frozenset[str],
) -> None:
    """Label the wider pool, build today's lineup round and store it (once per date).

    An already stored lineup (idempotent by its cluster id) is reused and nothing is
    labelled. ``remaining`` is what is left of ``daily_max_headlines`` after the clusters.
    A pool headline that fails (error, or no model answered) is skipped on its own;
    a stub verdict in live mode still raises. Never logs headline text.
    """
    cluster_id = lineup_cluster_id(local_date)
    stored = db.get_round_by_cluster(db_path, cluster_id)
    if stored is not None:
        result.n_rounds_existing += 1
        result.lineup_round_id = stored.round_id
        result.lineup_diversity = db.get_round_diversity(db_path, stored.round_id)
        if stored.status == db.STATUS_READY:
            (fallback if stored.round_id in kept else ready).append(stored.round_id)
        return

    items = select_pool(
        in_window, min(settings.daily_lineup_pool, max(remaining, 0)), cluster_links
    )
    for item in items:
        try:
            verdict = classify_fn(item.title)
        except Exception as exc:  # noqa: BLE001 - one headline must not fail the run
            result.n_pool_failed += 1
            log.warning("lineup pool headline skipped: %s", type(exc).__name__)
            continue
        if live and is_stub_verdict(verdict):
            raise StubVerdictError("stub verdict in live mode")
        if not verdict.votes:
            result.n_pool_failed += 1
            continue
        result.n_pool_labelled += 1
        if not verdict.disputed:
            lineup_pool.append(
                _lineup_headline(
                    item, verdict, story_of.get(item.link, item.link), len(lineup_pool)
                )
            )

    lineup = build_lineup(
        lineup_pool,
        used_techniques=db.recent_daily_techniques(db_path, local_date),
        rng_seed=cluster_id,
    )
    if lineup is None:
        return
    by_link = {item.link: item for item in in_window}
    rows = []
    for headline in lineup.headlines:
        item = by_link[headline.link]
        rows.append(
            {
                "text": item.title,
                "outlet": feed_names.get(item.outlet, item.outlet),
                "link": item.link,
                "published": item.published.astimezone(UTC)
                .isoformat()
                .replace("+00:00", "Z"),
                "declared_leaning": item.declared_leaning,
            }
        )
    inserted = db.insert_candidate_round(
        db_path,
        event=LINEUP_EVENT,
        items=rows,
        references=[h.reference for h in lineup.headlines],
        cluster_id=cluster_id,
        event_date=local_date,
        source_note=LINEUP_NOTE if lineup.distinct_stories else LINEUP_NOTE_SHARED_STORY,
        n_outlets=3,
    )
    if inserted.created:
        result.n_rounds_new += 1
    else:
        result.n_rounds_existing += 1
    result.lineup_round_id = inserted.round_id
    if inserted.status == db.STATUS_READY:
        result.lineup_diversity = inserted.diversity or None
        ready.append(inserted.round_id)


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
    rebuild: bool = False,
) -> LiveRoundsResult:
    """Fetch live feeds, label the best clusters and store them as rounds.

    Labelling is the only paid step, so it is capped: at most
    ``settings.daily_max_clusters`` clusters, ``settings.daily_max_per_cluster`` headlines
    per cluster (newest per outlet first) and ``settings.daily_max_headlines`` headlines in
    total (the budget is shared fairly between the clusters). Clusters already stored are
    not labelled again; the verdict cache keeps re-labelling free. ``classify_fn`` defaults
    to the one core; in live mode a stub verdict raises ``StubVerdictError`` and, with the
    default core, a missing key or an unwritable verdict cache fails before any model is
    called. Each cluster becomes a round whose three headlines have the most distinct
    reference techniques. With ``settings.daily_lineup_pool > 0`` a wider pool of up to that
    many in-window headlines (round-robin across outlets, not already chosen for a cluster,
    within what is left of ``daily_max_headlines``) is labelled too, and one "lineup" round
    (``lineup-<local_date>``: three headlines from three different outlets and, when
    possible, stories) is built from the cluster and pool headlines (the default pool is 0:
    event rounds only). Finally the best ready round is designated the daily challenge for
    ``local_date``: a round with a reference technique other than ``no_technique`` first,
    then the most diverse, most novel, most outlets, freshest (``db.pick_daily_challenge``).

    ``rebuild`` (``--rebuild-today``): once the feeds were fetched and clustered, today's
    unanswered rounds and today's designation are deleted (``db.reset_day``), so their
    clusters are labelled and stored again. Today's rounds with answers or disputes are
    kept but only re-picked when no other ready round exists.
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
    if min_outlets < 1:
        raise ValueError(f"min_outlets must be at least 1, got {min_outlets}")
    all_clusters = cluster_items(
        in_window, threshold=threshold, window_hours=window_hours
    )
    candidates = [c for c in all_clusters if c.n_outlets >= min_outlets]
    # Story of every in-window headline (singletons included): the lineup uses it to
    # prefer three different stories.
    story_of = {item.link: c.cluster_id for c in all_clusters for item in c.items}
    result.n_candidates = len(candidates)
    top = rank_clusters(candidates)[: settings.daily_max_clusters]
    result.n_clusters_used = len(top)

    kept: frozenset[str] = frozenset()
    if rebuild:
        # Only now: an outage of every feed must not leave the day without its rounds.
        start, end = _local_day_bounds(settings, local_date)
        reset = db.reset_day(
            db_path,
            local_date,
            created_from=start,
            created_to=end,
            core_mode=settings.core_mode,
        )
        kept = reset.kept_round_ids
        result.n_rounds_deleted = reset.deleted
        result.n_rounds_kept = reset.kept

    feed_names = {f.outlet: f.name for f in feeds}
    pool: list[str] = []
    fallback: list[str] = []  # kept (answered) rounds: used only if nothing else is ready
    pending: list[Cluster] = []
    for cluster in top:
        stored = db.get_round_by_cluster(db_path, cluster.cluster_id)
        if stored is None:
            pending.append(cluster)
            continue
        result.n_rounds_existing += 1
        if stored.status == db.STATUS_READY:
            (fallback if stored.round_id in kept else pool).append(stored.round_id)

    remaining = settings.daily_max_headlines
    cluster_links: set[str] = set()  # headlines chosen for clusters (not repeated in the pool)
    lineup_pool: list[LineupHeadline] = []
    for index, cluster in enumerate(pending):
        if remaining < MIN_LABELLED_PER_CLUSTER:
            break
        share = remaining // (len(pending) - index)
        limit = min(
            len(cluster.items),
            settings.daily_max_per_cluster,
            max(MIN_LABELLED_PER_CLUSTER, share),
            remaining,
        )
        chosen = select_headlines(cluster, limit)
        verdicts: list[Verdict] = []
        for item in chosen:
            verdict = classify_fn(item.title)
            result.n_labelled += 1
            remaining -= 1
            if live and is_stub_verdict(verdict):
                raise StubVerdictError("stub verdict in live mode")
            verdicts.append(verdict)
        for item, verdict in zip(chosen, verdicts, strict=True):
            cluster_links.add(item.link)
            if verdict.votes and not verdict.disputed:
                lineup_pool.append(
                    _lineup_headline(
                        item, verdict, cluster.cluster_id, len(lineup_pool)
                    )
                )
        if any(not v.votes for v in verdicts):
            # No model answered for some headline: do not freeze a degraded round.
            result.n_unlabelled_clusters += 1
            log.warning("cluster %s: no model verdict, skipped", cluster.cluster_id)
            continue
        inserted = db.insert_candidate_round(
            db_path,
            event=event_title([i.title for i in cluster.items], cluster.top_terms, settings),
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

    if settings.daily_lineup_pool <= 0 and kept:
        # Lineups are off: an answered lineup kept by --rebuild-today is not a candidate,
        # except as the last resort when no other round is ready.
        old_lineup = db.get_round_by_cluster(db_path, lineup_cluster_id(local_date))
        if (
            old_lineup is not None
            and old_lineup.status == db.STATUS_READY
            and old_lineup.round_id in kept
        ):
            fallback.append(old_lineup.round_id)
            result.n_rounds_existing += 1
    if settings.daily_lineup_pool > 0:
        _lineup_round(
            db_path,
            settings,
            result,
            local_date=local_date,
            in_window=in_window,
            story_of=story_of,
            feed_names=feed_names,
            classify_fn=classify_fn,
            live=live,
            remaining=remaining,
            cluster_links=cluster_links,
            lineup_pool=lineup_pool,
            ready=pool,
            fallback=fallback,
            kept=kept,
        )

    n_ready = len(pool) + len(fallback)
    result.n_ready = n_ready
    result.n_ineligible = result.n_rounds_new + result.n_rounds_existing - n_ready
    result.ready_round_ids = pool + fallback
    result.daily_round_id = db.pick_daily_challenge(
        db_path, local_date, pool, settings.core_mode
    ) or db.pick_daily_challenge(db_path, local_date, fallback, settings.core_mode)
    if result.daily_round_id is not None:
        result.daily_diversity = db.get_round_diversity(db_path, result.daily_round_id)
        result.daily_has_technique = db.round_has_technique(
            db_path, result.daily_round_id
        )
    if (result.n_unlabelled_clusters or result.n_pool_failed) and not (
        result.n_rounds_new or result.n_rounds_existing or result.n_pool_labelled
    ):
        raise LabellingUnavailableError("no cluster could be labelled")
    log.info("daily rounds: %s", result.counts())
    if result.daily_has_technique is False:
        log.warning(
            "daily challenge has no reference technique: no ready event round of the "
            "day had one (nothing was relabelled)"
        )
    elif result.daily_diversity is not None and result.daily_diversity < 3:
        log.warning(
            "daily challenge has only %d distinct technique(s): the labelled pool "
            "lacked variety",
            result.daily_diversity,
        )
    return result
