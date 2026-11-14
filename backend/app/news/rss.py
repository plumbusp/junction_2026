"""Polite RSS/Atom fetching and parsing.

Only title, link, outlet, published time and the outlet's declared leaning are
kept. Article bodies and summaries are never stored.

Fetching rules: 10 s timeout, 2 MB response cap, User-Agent ``spot-the-spin/0.1``,
one request per feed per run plus at most one retry on a transient error, then
the feed is skipped and logged.
"""

from __future__ import annotations

import calendar
import html
import logging
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime

import feedparser
import httpx

from app.news.feeds import Feed

log = logging.getLogger(__name__)

USER_AGENT = "spot-the-spin/0.1"
TIMEOUT_SECONDS = 10.0
MAX_BYTES = 2 * 1024 * 1024
RETRY_DELAY_SECONDS = 2.0
RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})

_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class NewsItem:
    title: str
    link: str
    outlet: str
    published: datetime  # timezone-aware, UTC
    declared_leaning: str  # attribute of the outlet, not of this headline

    def to_dict(self) -> dict[str, str]:
        return {
            "title": self.title,
            "link": self.link,
            "outlet": self.outlet,
            "published": self.published.astimezone(UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "declared_leaning": self.declared_leaning,
        }


class FeedFetchError(Exception):
    """A feed could not be downloaded. ``retryable`` says whether one retry makes sense."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass
class FetchReport:
    items: list[NewsItem] = field(default_factory=list)
    failed_outlets: list[str] = field(default_factory=list)
    fetched_outlets: list[str] = field(default_factory=list)


def clean_title(raw: str) -> str:
    """Strip tags, unescape entities and collapse whitespace."""
    text = html.unescape(_TAG.sub(" ", raw or ""))
    return _SPACE.sub(" ", text).strip()


def _entry_time(entry: feedparser.FeedParserDict) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime.fromtimestamp(calendar.timegm(parsed), tz=UTC)


def parse_feed(content: bytes, feed: Feed) -> list[NewsItem]:
    """Parse raw RSS/Atom bytes into NewsItems for ``feed``.

    Entries without a title, an http(s) link or a date are skipped. Links are
    de-duplicated within the feed. ``content`` must be bytes: feedparser treats
    a str that looks like a URL as something to fetch, which we never want here.
    """
    if not isinstance(content, bytes):
        raise TypeError("parse_feed expects bytes, not a URL or str")
    parsed = feedparser.parse(content)
    if parsed.get("bozo") and not parsed.entries:
        log.warning(
            "feed %s: unparseable content (%s)",
            feed.outlet,
            type(parsed.get("bozo_exception")).__name__,
        )
        return []

    items: list[NewsItem] = []
    seen_links: set[str] = set()
    skipped = 0
    for entry in parsed.entries:
        title = clean_title(entry.get("title", ""))
        link = (entry.get("link") or "").strip()
        published = _entry_time(entry)
        if (
            not title
            or not link.startswith(("http://", "https://"))
            or published is None
        ):
            skipped += 1
            continue
        if link in seen_links:
            continue
        seen_links.add(link)
        items.append(
            NewsItem(
                title=title,
                link=link,
                outlet=feed.outlet,
                published=published,
                declared_leaning=feed.leaning,
            )
        )
    if skipped:
        log.info(
            "feed %s: skipped %d entries without title, link or date",
            feed.outlet,
            skipped,
        )
    return items


def make_client() -> httpx.Client:
    return httpx.Client(
        timeout=TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )


def download(feed: Feed, client: httpx.Client, *, max_bytes: int = MAX_BYTES) -> bytes:
    """Download one feed body, enforcing the size cap while streaming."""
    try:
        with client.stream("GET", feed.url) as response:
            if response.status_code != 200:
                raise FeedFetchError(
                    f"HTTP {response.status_code}",
                    retryable=response.status_code in RETRYABLE_STATUS,
                )
            declared = response.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > max_bytes:
                raise FeedFetchError(
                    f"declared size {declared} exceeds cap", retryable=False
                )
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise FeedFetchError(
                        f"body exceeds {max_bytes} bytes", retryable=False
                    )
            return bytes(body)
    except httpx.TransportError as exc:
        raise FeedFetchError(type(exc).__name__, retryable=True) from exc


def fetch_feed(
    feed: Feed,
    client: httpx.Client,
    *,
    sleep: Callable[[float], None] = time.sleep,
    max_bytes: int = MAX_BYTES,
) -> bytes | None:
    """Download a feed with at most one retry on a transient error; None on failure."""
    for attempt in (1, 2):
        try:
            return download(feed, client, max_bytes=max_bytes)
        except FeedFetchError as exc:
            if attempt == 1 and exc.retryable:
                log.info("feed %s: %s, retrying once", feed.outlet, exc)
                sleep(RETRY_DELAY_SECONDS)
                continue
            log.warning("feed %s: skipped (%s)", feed.outlet, exc)
            return None
    return None


def fetch_items(
    feeds: Iterable[Feed],
    client: httpx.Client | None = None,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> FetchReport:
    """Fetch and parse every feed once. Links are de-duplicated across feeds."""
    report = FetchReport()
    own_client = client is None
    client = client or make_client()
    seen_urls: set[str] = set()
    seen_links: set[str] = set()
    try:
        for feed in feeds:
            if feed.url in seen_urls:
                continue
            seen_urls.add(feed.url)
            body = fetch_feed(feed, client, sleep=sleep)
            if body is None:
                report.failed_outlets.append(feed.outlet)
                continue
            report.fetched_outlets.append(feed.outlet)
            for item in parse_feed(body, feed):
                if item.link not in seen_links:
                    seen_links.add(item.link)
                    report.items.append(item)
    finally:
        if own_client:
            client.close()
    return report
