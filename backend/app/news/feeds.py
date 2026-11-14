"""Configured news outlets for the real-news source.

The ``leaning`` of an outlet is a *declared attribute of the source as a whole*,
approximated from public media-bias ratings (for example AllSides). It is never
a judgement about any individual headline, and the UI must show it with that
caveat (``LEANING_CAVEAT``).

The list mixes straight wire-style outlets with tabloids, cable-news sites and partisan
outlets on both sides, so that one event is covered by many differently worded headlines.
Where an outlet publishes several section feeds, one broad feed is used: an outlet id
appears once, so a round never shows "three outlets" that are really one.

Feed URLs are public RSS/Atom endpoints, live-checked with plain GETs on 2026-11-14 (each
parsed and had items newer than 3 days; CBS dates some items a few hours in the future,
which the window filter simply holds back). Checked and dropped: Salon and every CNN feed
(frozen for years), USA Today (HTTP 402), Politico picks and RedState (HTTP 403), The
Spectator, ITV and GB News (HTTP 404), The Daily Beast (nothing newer than two days),
Common Dreams, HuffPost and the LA Times world feed (too few fresh items), and the WSJ
world feed (frozen since 2025). State-run outlets are deliberately not listed. A feed can
still break any day: a run reports failed outlets and carries on with the rest.
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
    Feed("slate", "Slate", "https://slate.com/feeds/all.rss", "left"),
    Feed("vox", "Vox", "https://www.vox.com/rss/index.xml", "left"),
    Feed("mother_jones", "Mother Jones", "https://www.motherjones.com/feed/", "left"),
    Feed("intercept", "The Intercept", "https://theintercept.com/feed/?rss", "left"),
    Feed("jacobin", "Jacobin", "https://jacobin.com/feed", "left"),
    # lean left
    Feed(
        "guardian",
        "The Guardian",
        "https://www.theguardian.com/international/rss",
        "lean_left",
    ),
    Feed("npr", "NPR", "https://feeds.npr.org/1001/rss.xml", "lean_left"),
    Feed(
        "cbs_news", "CBS News", "https://www.cbsnews.com/latest/rss/main", "lean_left"
    ),
    Feed(
        "abc_news", "ABC News", "https://abcnews.go.com/abcnews/topstories", "lean_left"
    ),
    Feed(
        "nbc_news",
        "NBC News",
        "https://feeds.nbcnews.com/nbcnews/public/news",
        "lean_left",
    ),
    Feed(
        "nyt",
        "The New York Times",
        "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
        "lean_left",
    ),
    Feed(
        "atlantic",
        "The Atlantic",
        "https://www.theatlantic.com/feed/all/",
        "lean_left",
    ),
    Feed(
        "axios",
        "Axios",
        "https://api.axios.com/feed/",
        "lean_left",
    ),
    Feed(
        "mirror", "Daily Mirror", "https://www.mirror.co.uk/news/?service=rss", "lean_left"
    ),
    Feed(
        "independent",
        "The Independent",
        "https://www.independent.co.uk/news/rss",
        "lean_left",
    ),
    # center
    Feed("bbc_news", "BBC News", "https://feeds.bbci.co.uk/news/rss.xml", "center"),
    Feed("the_hill", "The Hill", "https://thehill.com/feed/", "center"),
    Feed(
        "sky_news",
        "Sky News",
        "https://feeds.skynews.com/feeds/rss/world.xml",
        "center",
    ),
    Feed("newsweek", "Newsweek", "https://www.newsweek.com/rss", "center"),
    Feed("dw", "Deutsche Welle", "https://rss.dw.com/rdf/rss-en-all", "center"),
    Feed(
        "al_jazeera",
        "Al Jazeera",
        "https://www.aljazeera.com/xml/rss/all.xml",
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
    Feed(
        "daily_mail",
        "Daily Mail",
        "https://www.dailymail.co.uk/news/index.rss",
        "lean_right",
    ),
    Feed("the_sun", "The Sun", "https://www.thesun.co.uk/news/feed/", "lean_right"),
    Feed(
        "washington_examiner",
        "Washington Examiner",
        "https://www.washingtonexaminer.com/feed",
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
    Feed(
        "daily_wire", "The Daily Wire", "https://www.dailywire.com/feeds/rss.xml", "right"
    ),
    Feed("daily_caller", "Daily Caller", "https://dailycaller.com/feed/", "right"),
    Feed("federalist", "The Federalist", "https://thefederalist.com/feed/", "right"),
    Feed(
        "national_review",
        "National Review",
        "https://www.nationalreview.com/feed/",
        "right",
    ),
    Feed(
        "newsmax", "Newsmax", "https://www.newsmax.com/rss/Newsfront/16/", "right"
    ),
    Feed(
        "the_blaze", "The Blaze", "https://www.theblaze.com/feeds/feed.rss", "right"
    ),
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
