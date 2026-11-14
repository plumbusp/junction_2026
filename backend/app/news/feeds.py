"""Configured news outlets for the real-news source.

The ``leaning`` of an outlet is a *declared attribute of the source as a whole*,
approximated from public media-bias ratings (for example AllSides). It is never
a judgement about any individual headline, and the UI must show it with that
caveat (``LEANING_CAVEAT``).

Feed URLs are public RSS/Atom endpoints. They were not live-checked when this
list was written; the first live run reports any that fail.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

LEANINGS = ("left", "lean_left", "center", "lean_right", "right")

LEANING_CAVEAT = (
    "Declared leaning of the outlet as a whole, approximated from public media-bias "
    "ratings. It describes the source, not any individual headline."
)

_OUTLET_ID = re.compile(r"^[a-z0-9_]+$")


@dataclass(frozen=True)
class Feed:
    outlet: str  # stable id, e.g. "bbc_news"
    name: str  # display name
    url: str  # RSS or Atom endpoint
    leaning: str  # one of LEANINGS; declared attribute of the source
    language: str = "en"

    def __post_init__(self) -> None:
        if not _OUTLET_ID.match(self.outlet):
            raise ValueError(f"outlet id must match [a-z0-9_]+, got {self.outlet!r}")
        if self.leaning not in LEANINGS:
            raise ValueError(
                f"unknown leaning {self.leaning!r}; expected one of {LEANINGS}"
            )
        if not self.name.strip():
            raise ValueError("feed name must not be empty")


FEEDS: tuple[Feed, ...] = (
    # left
    Feed(
        "huffpost",
        "HuffPost",
        "https://www.huffpost.com/section/front-page/feed",
        "left",
    ),
    # lean left
    Feed(
        "guardian", "The Guardian", "https://www.theguardian.com/world/rss", "lean_left"
    ),
    Feed("npr", "NPR", "https://feeds.npr.org/1001/rss.xml", "lean_left"),
    Feed(
        "cbs_news", "CBS News", "https://www.cbsnews.com/latest/rss/main", "lean_left"
    ),
    # center
    Feed(
        "bbc_news", "BBC News", "https://feeds.bbci.co.uk/news/world/rss.xml", "center"
    ),
    Feed("the_hill", "The Hill", "https://thehill.com/feed/", "center"),
    Feed(
        "wsj_world",
        "The Wall Street Journal",
        "https://feeds.a.dj.com/rss/RSSWorldNews.xml",
        "center",
    ),
    Feed(
        "sky_news",
        "Sky News",
        "https://feeds.skynews.com/feeds/rss/world.xml",
        "center",
    ),
    # lean right
    Feed("ny_post", "New York Post", "https://nypost.com/feed/", "lean_right"),
    Feed(
        "washington_times",
        "The Washington Times",
        "https://www.washingtontimes.com/rss/headlines/news/",
        "lean_right",
    ),
    Feed(
        "telegraph",
        "The Telegraph",
        "https://www.telegraph.co.uk/rss.xml",
        "lean_right",
    ),
    # right
    Feed(
        "fox_news",
        "Fox News",
        "https://moxie.foxnews.com/google-publisher/latest.xml",
        "right",
    ),
    Feed("breitbart", "Breitbart", "https://feeds.feedburner.com/breitbart", "right"),
)


def check_unique(feeds: tuple[Feed, ...] | list[Feed]) -> None:
    """Raise ValueError if two feeds share an outlet id or a URL."""
    outlets = [f.outlet for f in feeds]
    urls = [f.url for f in feeds]
    if len(set(outlets)) != len(outlets):
        raise ValueError("duplicate outlet id in feed list")
    if len(set(urls)) != len(urls):
        raise ValueError("duplicate feed URL in feed list")


check_unique(FEEDS)
