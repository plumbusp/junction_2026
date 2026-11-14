"""Lineup rounds in the live pipeline: wider labelled pool, one lineup per date, daily ranking."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import pytest
from app import db
from app.config import Settings, load_settings
from app.core.aggregate import aggregate
from app.core.stub import stub_classify
from app.news import job
from app.news.cluster import candidate_clusters, filter_window
from app.news.rss import NewsItem

from tests.db.rounds_fixture import stub_verdict
from tests.news.conftest import FIXTURE_NOW

DAY = "2026-11-13"
LINEUP_ID = f"lineup-{DAY}"
CYCLE = ("loaded_language", "doubt", "appeal_to_fear", "vague_authority", "no_technique")


class ByTitle:
    """Labels every headline; titles in ``clean_titles`` are always clean."""

    def __init__(self, clean_titles: frozenset[str] = frozenset()) -> None:
        self.clean_titles = clean_titles
        self.calls = 0

    def __call__(self, text: str):
        self.calls += 1
        if text in self.clean_titles:
            return stub_verdict("no_technique", [])
        technique = CYCLE[self.calls % len(CYCLE)]
        return stub_verdict(technique, [] if technique == "no_technique" else [text.split()[0]])


def _settings(**overrides) -> Settings:
    # Lineups are off by default: these tests switch them on explicitly.
    params = {
        "core_mode": "stub",
        "daily_max_clusters": 5,
        "daily_max_headlines": 200,
        "daily_lineup_pool": 120,
    }
    params.update(overrides)
    return Settings(**params)


def _run(tmp_db_path, fixture_feeds, labeller, *, rebuild=False, **overrides):
    feeds, client = fixture_feeds()
    return job.run_live_rounds(
        tmp_db_path,
        _settings(**overrides),
        local_date=DAY,
        feeds=feeds,
        client=client,
        now=FIXTURE_NOW,
        classify_fn=labeller,
        rebuild=rebuild,
    )


def _cluster_titles() -> frozenset[str]:
    _, items, now = job.load_fixtures()
    clusters = candidate_clusters(filter_window(items, now))
    return frozenset(i.title for c in clusters for i in c.items)


def _lineup_rows(db_path) -> list:
    with db.connect(db_path) as conn:
        return conn.execute(
            "SELECT i.* FROM round_items i JOIN rounds r ON r.round_id = i.round_id"
            " WHERE r.cluster_id = ? ORDER BY i.position",
            (LINEUP_ID,),
        ).fetchall()


def _item(n: int, outlet: str, hour: int) -> NewsItem:
    return NewsItem(
        f"t{n}", f"https://{outlet}.example/{n}", outlet,
        datetime(2026, 11, 13, hour, tzinfo=UTC), "center",
    )  # fmt: skip


def test_pool_is_round_robin_across_outlets_newest_first_and_skips_excluded_links() -> None:
    items = [
        _item(1, "a", 10), _item(2, "a", 11), _item(3, "a", 12),
        _item(4, "b", 9), _item(5, "c", 8),
    ]  # fmt: skip

    chosen = job.select_pool(items, 4, exclude_links={"https://a.example/3"})

    assert {i.outlet for i in chosen[:3]} == {"a", "b", "c"}  # one per outlet first
    assert chosen[0].title == "t2"  # the newest of outlet a that is not excluded
    assert len(chosen) == 4 and "t3" not in {i.title for i in chosen}
    assert job.select_pool(items, 0) == []
    assert len(job.select_pool(items, 99)) == 5


def test_pipeline_stores_one_lineup_with_three_outlets_and_honest_note(
    tmp_db_path, fixture_feeds, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    result = _run(tmp_db_path, fixture_feeds, ByTitle(_cluster_titles()))

    stored = db.get_round_by_cluster(tmp_db_path, LINEUP_ID)
    assert stored is not None and stored.status == db.STATUS_READY
    assert result.lineup_round_id == stored.round_id
    lineup = db.get_round(tmp_db_path, stored.round_id)
    assert lineup.event == job.LINEUP_EVENT
    assert len({i.outlet for i in lineup.items}) == 3
    for phrase in ("different stories", "outlets", "real RSS headlines", "describes the outlet"):
        assert phrase in lineup.source_note
    with db.connect(tmp_db_path) as conn:
        row = conn.execute(
            "SELECT n_outlets, diversity, techniques FROM rounds WHERE round_id = ?",
            (stored.round_id,),
        ).fetchone()
    assert row["n_outlets"] == 3
    assert row["diversity"] == len(json.loads(row["techniques"]))
    # The three stored links are pairwise different stories (clusters of the fixtures).
    assert len({r["link"] for r in _lineup_rows(tmp_db_path)}) == 3
    counts = result.counts()
    assert counts["lineup_diversity"] == row["diversity"] >= 2
    assert counts["pool_labelled"] > 0 and counts["daily_diversity"] >= 1
    assert all(isinstance(v, int) for v in counts.values())  # counts only, no text
    _, items, _ = job.load_fixtures()
    assert not any(item.title in caplog.text for item in items)


def test_pool_headlines_are_not_the_cluster_headlines_labelled_twice(
    tmp_db_path, fixture_feeds
) -> None:
    labeller = ByTitle()
    seen: list[str] = []

    def counting(text: str):
        seen.append(text)
        return labeller(text)

    result = _run(tmp_db_path, fixture_feeds, counting)

    assert len(seen) == len(set(seen))  # no headline sent to classify twice
    assert result.n_labelled + result.n_pool_labelled == len(seen)


def test_daily_prefers_a_diverse_lineup_over_clean_event_rounds(
    tmp_db_path, fixture_feeds
) -> None:
    result = _run(tmp_db_path, fixture_feeds, ByTitle(_cluster_titles()))

    # Every event round is all-clean (diversity 1); the lineup mixes in other techniques.
    assert result.lineup_diversity == 3
    assert result.daily_round_id == db.get_round_by_cluster(tmp_db_path, LINEUP_ID).round_id
    assert result.daily_diversity == 3
    assert result.counts()["lineup_diversity"] == result.counts()["daily_diversity"] == 3


def test_a_lineup_that_was_a_daily_is_not_picked_again(tmp_db_path, fixture_feeds) -> None:
    first = _run(tmp_db_path, fixture_feeds, ByTitle(_cluster_titles()))
    lineup_id = first.daily_round_id

    again = db.pick_daily_challenge(tmp_db_path, "2026-11-14", [lineup_id], "stub")

    assert again is None


def test_second_run_for_the_same_date_creates_no_second_lineup_and_labels_nothing(
    tmp_db_path, fixture_feeds
) -> None:
    labeller = ByTitle(_cluster_titles())
    first = _run(tmp_db_path, fixture_feeds, labeller)
    calls = labeller.calls

    again = _run(tmp_db_path, fixture_feeds, labeller)

    assert labeller.calls == calls and again.n_pool_labelled == 0
    assert again.lineup_round_id == first.lineup_round_id
    assert again.lineup_diversity == first.lineup_diversity
    with db.connect(tmp_db_path) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM rounds WHERE cluster_id LIKE 'lineup-%'"
        ).fetchone()[0]
    assert n == 1


def test_rebuild_today_replaces_an_unanswered_lineup(
    tmp_db_path, fixture_feeds, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(db, "_now", lambda: "2026-11-13T12:00:00Z")  # inside DAY
    first = _run(tmp_db_path, fixture_feeds, ByTitle(_cluster_titles()))

    rebuilt = _run(tmp_db_path, fixture_feeds, ByTitle(), rebuild=True)

    assert rebuilt.n_rounds_deleted == 3  # two event rounds and the lineup
    assert rebuilt.n_rounds_new == 3 and rebuilt.n_pool_labelled > 0
    assert rebuilt.lineup_round_id == first.lineup_round_id  # same id, rebuilt content
    assert len(_lineup_rows(tmp_db_path)) == 3
    with db.connect(tmp_db_path) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM rounds WHERE cluster_id LIKE 'lineup-%'"
        ).fetchone()[0]
    assert n == 1


def test_rebuild_with_lineups_off_removes_an_unanswered_old_lineup(
    tmp_db_path, fixture_feeds, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(db, "_now", lambda: "2026-11-13T12:00:00Z")  # inside DAY
    first = _run(tmp_db_path, fixture_feeds, ByTitle(_cluster_titles()))
    assert first.daily_round_id == first.lineup_round_id  # the old lineup was the daily

    rebuilt = _run(tmp_db_path, fixture_feeds, ByTitle(), rebuild=True, daily_lineup_pool=0)

    assert db.get_round_by_cluster(tmp_db_path, LINEUP_ID) is None
    assert rebuilt.lineup_round_id is None
    assert rebuilt.daily_round_id not in (None, first.lineup_round_id)


def test_rebuild_with_lineups_off_does_not_repick_an_answered_lineup(
    tmp_db_path, fixture_feeds, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(db, "_now", lambda: "2026-11-13T12:00:00Z")
    first = _run(tmp_db_path, fixture_feeds, ByTitle(_cluster_titles()))
    item = db.get_round(tmp_db_path, first.lineup_round_id).items[0]
    db.insert_answer(
        tmp_db_path, player_id="p1", round_id=first.lineup_round_id, item_id=item.item_id,
        technique="doubt", selected_words=[], technique_points=0, evidence_points=0, total=0,
    )  # fmt: skip

    rebuilt = _run(tmp_db_path, fixture_feeds, ByTitle(), rebuild=True, daily_lineup_pool=0)

    assert rebuilt.n_rounds_kept == 1
    assert first.lineup_round_id in db.list_ready_round_ids(tmp_db_path, "stub")  # kept
    assert rebuilt.daily_round_id not in (None, first.lineup_round_id)
    # Last resort only: with no event round ready, the kept lineup may be the daily.
    assert db.pick_daily_challenge(
        tmp_db_path, "2026-11-20", [first.lineup_round_id], "stub"
    ) == first.lineup_round_id


def test_lineup_pool_zero_disables_lineups(tmp_db_path, fixture_feeds) -> None:
    labeller = ByTitle()

    result = _run(tmp_db_path, fixture_feeds, labeller, daily_lineup_pool=0)

    assert result.n_pool_labelled == 0 and result.lineup_round_id is None
    assert result.counts()["lineup_diversity"] == 0
    assert db.get_round_by_cluster(tmp_db_path, LINEUP_ID) is None
    assert labeller.calls == result.n_labelled


def test_labelling_caps_cover_cluster_headlines_plus_pool(
    tmp_db_path, fixture_feeds
) -> None:
    labeller = ByTitle()
    capped = _run(
        tmp_db_path, fixture_feeds, labeller, daily_max_headlines=10, daily_lineup_pool=120
    )
    assert labeller.calls <= 10
    assert capped.n_labelled + capped.n_pool_labelled == labeller.calls

    small_pool = ByTitle()
    pool_capped = _run(
        tmp_db_path.with_name("pool.db"), fixture_feeds, small_pool, daily_lineup_pool=4
    )
    assert pool_capped.n_pool_labelled <= 4
    assert small_pool.calls - pool_capped.n_labelled <= 4


def test_a_failing_pool_headline_is_skipped_not_fatal(tmp_db_path, fixture_feeds) -> None:
    inner = ByTitle(_cluster_titles())
    cluster_titles = _cluster_titles()

    def flaky(text: str):
        if text not in cluster_titles and inner.calls % 4 == 0:
            inner.calls += 1
            raise TimeoutError("provider timeout")
        return inner(text)

    result = _run(tmp_db_path, fixture_feeds, flaky)

    assert result.n_pool_failed > 0 and result.n_pool_labelled > 0
    assert result.daily_round_id is not None


def test_nothing_labelled_at_all_fails_the_run(tmp_db_path, fixture_feeds) -> None:
    no_answer = aggregate([], n_models=3)  # what classify returns when every model fails

    with pytest.raises(job.LabellingUnavailableError):
        _run(tmp_db_path, fixture_feeds, lambda t: no_answer)

    assert db.list_ready_round_ids(tmp_db_path, "stub") == []


def test_live_mode_refuses_stub_verdicts_in_the_pool(tmp_db_path, fixture_feeds) -> None:
    with pytest.raises(job.StubVerdictError):
        _run(tmp_db_path, fixture_feeds, stub_classify, core_mode="live")


def test_settings_defaults_and_range_for_the_lineup_pool() -> None:
    defaults = load_settings({})
    assert defaults.daily_lineup_pool == 0  # event rounds only by default
    assert Settings().daily_lineup_pool == 0
    assert defaults.daily_max_headlines == 200
    assert (defaults.daily_max_clusters, defaults.daily_max_per_cluster) == (12, 8)
    assert load_settings({"DAILY_LINEUP_POOL": "120"}).daily_lineup_pool == 120
    assert load_settings({"DAILY_LINEUP_POOL": "0"}).daily_lineup_pool == 0
    assert load_settings({"DAILY_LINEUP_POOL": "400"}).daily_lineup_pool == 400
    for bad in ("401", "-1", "many"):
        with pytest.raises(ValueError):
            load_settings({"DAILY_LINEUP_POOL": bad})
