"""Event clustering: >= 3 distinct outlets within 48 h become candidates."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.news.cluster import candidate_clusters, cluster_items, filter_window, tokenize
from app.news.rss import NewsItem

T0 = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def item(outlet: str, title: str, hours: float = 0.0) -> NewsItem:
    slug = title.lower().replace(" ", "-")[:40]
    return NewsItem(
        title=title,
        link=f"https://{outlet}.example/{slug}",
        outlet=outlet,
        published=T0 + timedelta(hours=hours),
        declared_leaning="center",
    )


NOISE = [
    item("e", "City council approves new bike lanes downtown"),
    item("f", "Museum returns looted bronze sculptures"),
    item("g", "Star striker signs record transfer deal"),
]


def test_three_outlet_event_is_a_candidate() -> None:
    items = [
        item("a", "Central bank raises interest rates to curb inflation"),
        item("b", "Interest rates rise as central bank warns on inflation", 2),
        item("c", "Central bank hikes interest rates again", 5),
        *NOISE,
    ]

    candidates = candidate_clusters(items)

    assert len(candidates) == 1
    assert candidates[0].outlets == ["a", "b", "c"]


def test_two_outlet_event_is_not_a_candidate() -> None:
    items = [
        item("a", "Wildfire forces thousands to evacuate in Pine Valley"),
        item("b", "Pine Valley wildfire evacuation orders expand", 3),
        *NOISE,
    ]

    clusters = cluster_items(items)

    assert any(
        c.outlets == ["a", "b"] for c in clusters
    )  # grouped, but too few outlets
    assert candidate_clusters(items) == []


def test_three_headlines_from_one_outlet_count_as_one() -> None:
    items = [
        item("a", "Port strike shuts major ports over pay"),
        item("a", "Port strike enters second day over pay", 4),
        item("b", "Major ports shut as port strike over pay spreads", 6),
    ]

    assert candidate_clusters(items) == []


def test_headlines_further_apart_than_window_do_not_merge() -> None:
    items = [
        item("a", "Election commission certifies regional vote results"),
        item("b", "Election commission certifies regional vote", 30),
        item("c", "Regional vote results certified by election commission", 60),
    ]

    candidates = candidate_clusters(items, window_hours=48)

    assert candidates == []
    assert all(
        (c.last_published - c.first_published) <= timedelta(hours=48)
        for c in cluster_items(items)
    )


def test_unrelated_headlines_stay_apart_and_empty_input_is_empty() -> None:
    assert all(len(c.items) == 1 for c in cluster_items(NOISE))
    assert cluster_items([]) == []


def test_invalid_parameters_raise() -> None:
    with pytest.raises(ValueError):
        cluster_items(NOISE, threshold=0.0)
    with pytest.raises(ValueError):
        candidate_clusters(NOISE, min_outlets=0)


def test_filter_window_boundaries() -> None:
    now = T0
    inside = item("a", "inside", -47)
    too_old = item("b", "too old", -49)
    far_future = item("c", "far future", 5)

    assert filter_window([inside, too_old, far_future], now, 48) == [inside]


def test_tokenize_drops_stopwords_possessives_and_plurals() -> None:
    assert tokenize("The Bank's rates rise, says minister") == [
        "bank",
        "rate",
        "rise",
        "minister",
    ]
    assert tokenize("") == []


def test_cluster_id_is_stable_for_same_members() -> None:
    items = [
        item("a", "Central bank raises interest rates"),
        item("b", "Central bank raises interest rates again", 1),
        item("c", "Interest rates raised by central bank", 2),
    ]

    first = candidate_clusters(items)[0].cluster_id
    second = candidate_clusters(list(reversed(items)))[0].cluster_id

    assert first == second
