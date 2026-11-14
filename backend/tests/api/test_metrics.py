"""GET /api/metrics: 404 without real results, served as-is otherwise, stub refused."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient


def test_metrics_404_when_no_real_results(client: TestClient) -> None:
    res = client.get("/api/metrics")

    assert res.status_code == 404
    assert res.json()["error"] == "no_results"


def test_metrics_serves_latest_live_results_as_is(
    client: TestClient, metrics_path: Path
) -> None:
    data = {"schema_version": 1, "mode": "live", "sources": {"semeval21": {"n": 135}}}
    metrics_path.parent.mkdir(parents=True)
    metrics_path.write_text(json.dumps(data), encoding="utf-8")

    res = client.get("/api/metrics")

    assert res.status_code == 200
    assert res.json() == data


def test_metrics_refuses_a_stub_results_file(
    client: TestClient, metrics_path: Path
) -> None:
    metrics_path.parent.mkdir(parents=True)
    metrics_path.write_text(
        json.dumps({"mode": "stub", "sources": {}}), encoding="utf-8"
    )

    assert client.get("/api/metrics").status_code == 404
