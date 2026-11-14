"""The configured outlet list: sanity checks only (no network; liveness is checked by hand)."""

from __future__ import annotations

from collections import Counter

import pytest
from app.news.feeds import FEEDS, LEANING_CAVEAT, LEANINGS, Feed, check_unique


def test_outlet_ids_and_urls_are_unique_and_valid() -> None:
    check_unique(FEEDS)
    assert len({f.outlet for f in FEEDS}) == len(FEEDS)
    assert len({f.url for f in FEEDS}) == len(FEEDS)
    assert all(f.name.strip() for f in FEEDS)


def test_feed_count_is_in_the_planned_range() -> None:
    assert 25 <= len(FEEDS) <= 45


def test_every_feed_uses_https_and_a_declared_leaning() -> None:
    for feed in FEEDS:
        assert feed.url.startswith("https://"), feed.outlet
        assert feed.leaning in LEANINGS, feed.outlet
        assert feed.language == "en"


def test_every_leaning_is_represented_and_none_dominates() -> None:
    by_leaning = Counter(f.leaning for f in FEEDS)
    assert set(by_leaning) == set(LEANINGS)
    assert max(by_leaning.values()) <= len(FEEDS) // 2


def test_no_state_run_outlets_are_listed() -> None:
    banned = ("rt", "sputnik", "xinhua", "cgtn", "tass", "press_tv", "global_times")
    assert not {f.outlet for f in FEEDS} & set(banned)


def test_duplicates_and_bad_values_are_rejected() -> None:
    a = Feed("a", "A", "https://a.example/rss", "center")
    with pytest.raises(ValueError):
        check_unique([a, Feed("a", "A2", "https://a2.example/rss", "center")])
    with pytest.raises(ValueError):
        check_unique([a, Feed("b", "B", "https://a.example/rss", "center")])
    with pytest.raises(ValueError):
        Feed("c", "C", "https://c.example/rss", "centre")
    with pytest.raises(ValueError):
        Feed("Bad Id", "C", "https://c.example/rss", "center")


def test_leaning_caveat_names_the_source_not_the_headline() -> None:
    assert "not any individual headline" in LEANING_CAVEAT
