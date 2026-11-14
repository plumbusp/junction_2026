"""POST /api/analyze: exact-substring spans, input rules, URL path, core failure, rate limit."""

from __future__ import annotations

import pytest
from app.api import analyze as analyze_api
from app.core.stub import stub_classify
from app.security import ssrf
from fastapi.testclient import TestClient

from tests.api.conftest import ANALYZE_LIMIT

VERDICT_KEYS = {
    "technique",
    "evidence_spans",
    "explanation",
    "agreement",
    "n_models",
    "disputed",
    "votes",
}

TEXTS = [
    "Critics warn the reckless new tax will destroy small businesses overnight.",
    "Council slams commuters with fare rip-off as prices jump 20%",
    "The weather in the capital is mild this week.",
    "Experts say the shocking plan is a disaster waiting to happen!",
    "Is the government hiding the truth about the vaccine rollout?",
]


def test_analyze_spans_are_exact_substrings_of_the_returned_text(make_client) -> None:
    client = make_client(analyze_limit=100)
    seen_spans = 0
    for text in TEXTS:
        res = client.post("/api/analyze", json={"text": f"  {text} "})
        assert res.status_code == 200
        body = res.json()
        assert VERDICT_KEYS | {"text", "source_url", "title"} == set(body)
        assert body["text"] == text  # surrounding whitespace stripped
        assert body["source_url"] is None
        for span in body["evidence_spans"]:
            assert isinstance(span, str) and span and span in body["text"]
            seen_spans += 1
        for vote in body["votes"]:
            assert all(span in body["text"] for span in vote["evidence_spans"])
    assert seen_spans > 0  # the check above was not vacuous


@pytest.mark.parametrize(
    "payload",
    [{}, {"text": "   "}, {"text": "a", "url": "https://example.com/"}, {"text": 5}],
)
def test_analyze_needs_exactly_one_text_or_url(
    client: TestClient, payload: dict
) -> None:
    res = client.post("/api/analyze", json=payload)

    assert res.status_code == 422
    assert res.json()["error"] == "invalid_request"


def test_analyze_text_over_the_cap_is_413(client: TestClient) -> None:
    res = client.post("/api/analyze", json={"text": "word " * 1200})

    assert res.status_code == 413
    assert res.json()["error"] == "text_too_long"


def test_analyze_url_uses_extracted_headline_and_lead(make_client) -> None:
    html = (
        b"<html><head><title>Reckless tax will destroy small businesses</title></head><body>"
        b"<article><h1>Reckless tax will destroy small businesses</h1>"
        b"<p>The ministry says the change affects about four percent of firms and takes "
        b"effect in March, according to the published budget documents.</p></article>"
        b"</body></html>"
    )

    def fake_fetch(url: str) -> ssrf.FetchedPage:
        return ssrf.FetchedPage(
            url="https://news.example/tax", body=html, content_type="text/html"
        )

    client = make_client(fetch_url=fake_fetch)
    res = client.post("/api/analyze", json={"url": "https://news.example/tax"})

    assert res.status_code == 200
    body = res.json()
    assert body["source_url"] == "https://news.example/tax"
    assert body["title"] == "Reckless tax will destroy small businesses"
    assert "four percent of firms" in body["text"]
    assert VERDICT_KEYS <= set(body)
    assert all(span in body["text"] for span in body["evidence_spans"])
    assert body["technique"] == stub_classify(body["text"]).technique


def test_analyze_unsafe_url_is_400(make_client) -> None:
    client = make_client(fetch_url=ssrf.fetch_url)

    res = client.post("/api/analyze", json={"url": "http://example.com/"})

    assert res.status_code == 400
    assert res.json()["error"] == "unsafe_url"


def test_core_failure_is_503_without_details(client: TestClient, monkeypatch) -> None:
    def broken(text: str):
        raise RuntimeError("router said: key sk-test-123 rejected")

    monkeypatch.setattr(analyze_api, "classify", broken)
    res = client.post("/api/analyze", json={"text": "Some text."})

    assert res.status_code == 503
    assert res.json()["error"] == "core_unavailable"
    assert "sk-test" not in res.text and "Traceback" not in res.text


def test_rate_limit_returns_429_after_the_limit(client: TestClient) -> None:
    codes = [
        client.post("/api/analyze", json={"text": "Plain sentence."}).status_code
        for _ in range(ANALYZE_LIMIT + 1)
    ]

    assert codes[:ANALYZE_LIMIT] == [200] * ANALYZE_LIMIT
    assert codes[-1] == 429
    last = client.post("/api/analyze", json={"text": "Plain sentence."})
    assert last.json()["error"] == "rate_limited"
    assert int(last.headers["retry-after"]) >= 1
