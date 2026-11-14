"""API test client: stub core, temporary database, no real metrics file, no network."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.config import Settings
from app.main import create_app
from app.security import ssrf
from fastapi.testclient import TestClient

from tests.db.rounds_fixture import seed_worked_example

ANALYZE_LIMIT = 5


def _no_network_fetch(url: str) -> ssrf.FetchedPage:
    raise AssertionError(f"network access attempted in a test: {url}")


@pytest.fixture
def metrics_path(tmp_path: Path) -> Path:
    return tmp_path / "results" / "latest.json"


@pytest.fixture
def make_client(tmp_db_path: Path, metrics_path: Path):
    clients: list[TestClient] = []

    def make(**overrides) -> TestClient:
        params = {
            "db_path": tmp_db_path,
            "metrics_path": metrics_path,
            "analyze_limit": ANALYZE_LIMIT,
            "fetch_url": _no_network_fetch,
        }
        settings_fields = overrides.pop("settings", {})  # e.g. daily_job_hour=6
        params.update(overrides)
        app = create_app(
            Settings(core_mode="stub", db_path=tmp_db_path, **settings_fields), **params
        )
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()


@pytest.fixture
def seeded(tmp_db_path: Path):
    """The worked-example round (3 in play + 1 disputed headline kept out)."""
    return seed_worked_example(tmp_db_path)
