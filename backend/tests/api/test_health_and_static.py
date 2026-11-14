"""Health endpoints, static pages and error hygiene."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize("path", ["/health", "/api/health"])
def test_health_reports_ok_and_stub_mode_only(client: TestClient, path: str) -> None:
    res = client.get(path)

    assert res.status_code == 200
    assert res.json() == {"ok": True, "core_mode": "stub"}


@pytest.mark.parametrize("path", ["/", "/analyze/", "/metrics/", "/label/"])
def test_frontend_pages_are_served(client: TestClient, path: str) -> None:
    res = client.get(path)

    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert res.headers["x-content-type-options"] == "nosniff"


def test_page_without_slash_redirects(client: TestClient) -> None:
    res = client.get("/analyze?mock=1", follow_redirects=False)

    assert res.status_code == 307
    assert res.headers["location"] == "/analyze/?mock=1"


def test_unknown_api_path_is_json_404(client: TestClient) -> None:
    res = client.get("/api/does-not-exist")

    assert res.status_code == 404
    assert res.json()["error"] == "not_found"


def test_unhandled_error_is_500_json_without_trace(
    client: TestClient, monkeypatch
) -> None:
    from app import db

    def boom(*args, **kwargs):
        raise RuntimeError("internal detail /secret/path AI_ROUTER_API_KEY=xyz")

    monkeypatch.setattr(db, "list_ready_round_ids", boom)
    res = client.get("/api/round")

    assert res.status_code == 500
    assert res.json() == {"error": "internal_error", "detail": "Internal server error."}
    assert "secret" not in res.text and "Traceback" not in res.text


def test_oversized_body_is_413(client: TestClient) -> None:
    res = client.post(
        "/api/analyze",
        content=b'{"text": "' + b"a" * 70_000 + b'"}',
        headers={"Content-Type": "application/json"},
    )

    assert res.status_code == 413
    assert res.json()["error"] == "payload_too_large"
