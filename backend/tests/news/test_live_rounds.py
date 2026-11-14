"""Live pipeline (feeds -> labelled rounds -> daily challenge) with mocked HTTP and stub labels."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx
import pytest
from app import db
from app.config import Settings
from app.core.aggregate import aggregate
from app.core.stub import stub_classify
from app.news import job
from app.news.cluster import Cluster, candidate_clusters, filter_window
from app.news.feeds import Feed
from app.news.rss import NewsItem

from tests.db.rounds_fixture import stub_verdict
from tests.news.conftest import FIXTURE_NOW

DAY = "2026-11-13"


class Labeller:
    """Counts calls; every third headline is clean, so each cluster has a no_technique one."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, text: str):
        self.calls += 1
        if self.calls % 3 == 1:
            return stub_verdict("no_technique", [])
        return stub_verdict("loaded_language", [text.split()[0]])


def _settings(**overrides) -> Settings:
    # Lineups are off here: these tests cover the event rounds (see test_lineup_rounds.py).
    params = {
        "core_mode": "stub",
        "daily_max_clusters": 5,
        "daily_max_headlines": 24,
        "daily_lineup_pool": 0,
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


def _n_outlets(db_path, round_id: str) -> int:
    with db.connect(db_path) as conn:
        row = conn.execute(
            "SELECT n_outlets FROM rounds WHERE round_id = ?", (round_id,)
        ).fetchone()
    return row["n_outlets"]


def test_pipeline_stores_rounds_and_designates_the_widest_event(
    tmp_db_path, fixture_feeds, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    labeller = Labeller()

    result = _run(tmp_db_path, fixture_feeds, labeller)

    assert result.n_candidates == result.n_rounds_new == 2
    assert result.n_labelled == labeller.calls
    assert result.n_ready == 2
    assert result.daily_round_id in result.ready_round_ids
    assert (
        _n_outlets(tmp_db_path, result.daily_round_id) == 4
    )  # the 4-outlet story wins
    assert db.get_daily_challenge(tmp_db_path, DAY) == result.daily_round_id
    # Headline text is never logged.
    _, items, _ = job.load_fixtures()
    assert not any(item.title in caplog.text for item in items)


def test_headline_and_cluster_caps_are_respected(tmp_db_path, fixture_feeds) -> None:
    one = _run(tmp_db_path, fixture_feeds, Labeller(), daily_max_clusters=1)
    assert one.n_clusters_used == 1 and one.n_rounds_new == 1

    labeller = Labeller()
    capped = _run(
        tmp_db_path.with_name("capped.db"),
        fixture_feeds,
        labeller,
        daily_max_headlines=5,
    )
    assert capped.n_labelled == labeller.calls <= 5
    # The 5-headline budget cannot fill a second round of three: it is skipped, not overspent.
    assert capped.n_rounds_new == 1


def test_second_run_does_not_label_stored_clusters_again(
    tmp_db_path, fixture_feeds
) -> None:
    labeller = Labeller()
    first = _run(tmp_db_path, fixture_feeds, labeller)
    calls = labeller.calls

    again = _run(tmp_db_path, fixture_feeds, labeller)

    assert labeller.calls == calls
    assert again.n_labelled == 0 and again.n_rounds_existing == 2
    assert again.daily_round_id == first.daily_round_id


class Varied:
    """Cycles through five techniques, so every cluster can show distinct ones."""

    CYCLE = ("no_technique", "loaded_language", "doubt", "appeal_to_fear", "vague_authority")

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, text: str):
        technique = self.CYCLE[self.calls % len(self.CYCLE)]
        self.calls += 1
        return stub_verdict(technique, [] if technique == "no_technique" else [text.split()[0]])


def test_rounds_use_the_per_cluster_cap_and_record_diversity(
    tmp_db_path, fixture_feeds
) -> None:
    labeller = Varied()

    result = _run(tmp_db_path, fixture_feeds, labeller, daily_max_per_cluster=3)

    assert labeller.calls <= 3 * result.n_clusters_used
    assert result.daily_diversity == 3
    assert result.counts()["daily_diversity"] == 3


def _widest_cluster_titles() -> frozenset[str]:
    _, items, now = job.load_fixtures()
    clusters = candidate_clusters(filter_window(items, now))
    return frozenset(i.title for i in max(clusters, key=lambda c: c.n_outlets).items)


def test_default_settings_build_event_rounds_only(tmp_db_path, fixture_feeds) -> None:
    feeds, client = fixture_feeds()
    labeller = Varied()

    result = job.run_live_rounds(
        tmp_db_path,
        Settings(core_mode="stub"),  # every labelling setting at its default
        local_date=DAY,
        feeds=feeds,
        client=client,
        now=FIXTURE_NOW,
        classify_fn=labeller,
    )

    assert result.lineup_round_id is None and result.n_pool_labelled == 0
    assert labeller.calls == result.n_labelled
    with db.connect(tmp_db_path) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM rounds WHERE cluster_id LIKE 'lineup-%'"
        ).fetchone()[0]
    assert n == 0 and result.n_rounds_new == result.n_candidates


def test_daily_takes_the_round_with_a_technique_even_from_a_narrower_event(
    tmp_db_path, fixture_feeds
) -> None:
    widest = _widest_cluster_titles()

    other_calls = [0]

    def clean_in_the_widest(text: str):
        if text in widest:
            return stub_verdict("no_technique", [])
        technique = ("no_technique", "doubt", "loaded_language")[other_calls[0] % 3]
        other_calls[0] += 1
        return stub_verdict(technique, [] if technique == "no_technique" else [text.split()[0]])

    result = _run(tmp_db_path, fixture_feeds, clean_in_the_widest)

    assert result.n_ready == 2
    assert _n_outlets(tmp_db_path, result.daily_round_id) < 4  # not the widest, clean one
    assert result.daily_has_technique is True
    assert result.counts()["daily_has_technique"] == 1


def test_daily_without_any_technique_is_still_set_and_flagged(
    tmp_db_path, fixture_feeds, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    result = _run(
        tmp_db_path, fixture_feeds, lambda text: stub_verdict("no_technique", [])
    )

    assert result.daily_round_id is not None  # the day is never left empty
    assert db.get_daily_challenge(tmp_db_path, DAY) == result.daily_round_id
    assert result.daily_has_technique is False
    counts = result.counts()
    assert counts["daily_has_technique"] == 0 and counts["daily_diversity"] == 1
    assert "no reference technique" in caplog.text
    _, items, _ = job.load_fixtures()
    assert not any(item.title in caplog.text for item in items)
    # Nothing was relabelled: every stored reference is still the clean one.
    stored = db.get_round(tmp_db_path, result.daily_round_id)
    assert {i.reference.technique for i in stored.items} == {"no_technique"}


def test_selection_is_deterministic_across_identical_runs(
    tmp_db_path, fixture_feeds
) -> None:
    first = _run(tmp_db_path, fixture_feeds, Varied())
    other = _run(tmp_db_path.with_name("other.db"), fixture_feeds, Varied())

    def shape(path, round_id):
        stored = db.get_round(path, round_id)
        return stored.event, [(i.text, i.reference.technique) for i in stored.items]

    assert first.daily_round_id == other.daily_round_id
    assert shape(tmp_db_path, first.daily_round_id) == shape(
        tmp_db_path.with_name("other.db"), other.daily_round_id
    )


def test_rebuild_today_replaces_unanswered_rounds_and_keeps_answered_ones(
    tmp_db_path, fixture_feeds, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(db, "_now", lambda: "2026-11-13T12:00:00Z")  # fake clock, in DAY
    first = _run(tmp_db_path, fixture_feeds, Labeller())
    answered = first.daily_round_id
    item = db.get_round(tmp_db_path, answered).items[0]
    db.insert_answer(
        tmp_db_path,
        player_id="p1",
        round_id=answered,
        item_id=item.item_id,
        technique="doubt",
        selected_words=[],
        technique_points=0,
        evidence_points=0,
        total=0,
    )
    labeller = Varied()

    rebuilt = _run(tmp_db_path, fixture_feeds, labeller, rebuild=True)

    assert (rebuilt.n_rounds_deleted, rebuilt.n_rounds_kept) == (1, 1)
    assert rebuilt.n_rounds_new == 1 and labeller.calls > 0  # the deleted cluster is rebuilt
    assert rebuilt.n_ready == 2
    # The answered round stays playable but is not re-picked while another one exists.
    assert answered in db.list_ready_round_ids(tmp_db_path, "stub")
    assert rebuilt.daily_round_id not in (None, answered)
    assert db.get_daily_challenge(tmp_db_path, DAY) == rebuilt.daily_round_id
    assert rebuilt.daily_diversity == db.get_round_diversity(
        tmp_db_path, rebuilt.daily_round_id
    )


def test_live_mode_refuses_stub_verdicts(tmp_db_path, fixture_feeds) -> None:
    feeds, client = fixture_feeds()

    with pytest.raises(job.StubVerdictError):
        job.run_live_rounds(
            tmp_db_path,
            _settings(core_mode="live"),
            local_date=DAY,
            feeds=feeds,
            client=client,
            now=FIXTURE_NOW,
            classify_fn=stub_classify,
        )

    assert db.list_ready_round_ids(tmp_db_path, "stub") == []
    assert db.list_ready_round_ids(tmp_db_path, "live") == []


def test_live_mode_without_key_fails_before_any_network_call(
    tmp_db_path,
) -> None:
    def no_requests(request: httpx.Request) -> httpx.Response:
        raise AssertionError("fetched before the key check")

    client = httpx.Client(transport=httpx.MockTransport(no_requests))

    with pytest.raises(RuntimeError, match="AI_ROUTER_API_KEY"):
        job.run_live_rounds(
            tmp_db_path,
            _settings(core_mode="live"),
            local_date=DAY,
            feeds=[Feed("x", "X", "https://x.example/rss", "center")],
            client=client,
            now=FIXTURE_NOW,
        )


def test_all_feeds_down_is_a_failure(tmp_db_path, fixture_feeds) -> None:
    feeds, client = fixture_feeds(
        down={
            "civic_wire",
            "northgate_herald",
            "daily_ledger",
            "harbor_times",
            "frontier_post",
        }
    )

    with pytest.raises(job.NoItemsError):
        job.run_live_rounds(
            tmp_db_path,
            _settings(),
            local_date=DAY,
            feeds=feeds,
            client=client,
            now=FIXTURE_NOW,
            classify_fn=Labeller(),
        )


def test_clusters_without_any_model_answer_are_not_stored(
    tmp_db_path, fixture_feeds
) -> None:
    no_answer = aggregate(
        [], n_models=3
    )  # what classify returns when every model fails

    with pytest.raises(job.LabellingUnavailableError):
        _run(tmp_db_path, fixture_feeds, lambda text: no_answer)

    assert db.list_ready_round_ids(tmp_db_path, "stub") == []


def test_select_headlines_takes_one_per_outlet_first() -> None:
    def item(n: int, outlet: str) -> NewsItem:
        when = datetime(2026, 11, 13, 10 + n, tzinfo=UTC)
        return NewsItem(
            f"t{n}", f"https://{outlet}.example/{n}", outlet, when, "center"
        )

    cluster = Cluster(
        [item(0, "a"), item(1, "a"), item(2, "b"), item(3, "b"), item(4, "c")],
        cohesion=1.0,
        top_terms=[],
    )

    chosen = job.select_headlines(cluster, 3)

    assert sorted(i.outlet for i in chosen) == ["a", "b", "c"]
