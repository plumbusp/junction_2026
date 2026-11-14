"""SSRF rules for Analyze URL input. No network: fake resolver plus httpx MockTransport."""

from __future__ import annotations

import httpx
import pytest
from app.security import ssrf

PUBLIC_IP = "93.184.216.34"
HTML = b"<html><head><title>Headline</title></head><body><p>Lead paragraph.</p></body></html>"


def fake_resolver(table: dict[str, list[str]]):
    def resolve(host: str, port: int) -> list[str]:
        if host not in table:
            raise ssrf.FetchError("unresolvable")
        return table[host]

    return resolve


def mock_transport(routes: dict[str, httpx.Response]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return routes[str(request.url)]

    return httpx.MockTransport(handler)


def html_response(body: bytes = HTML) -> httpx.Response:
    return httpx.Response(
        200, headers={"content-type": "text/html; charset=utf-8"}, content=body
    )


def test_public_https_page_is_fetched() -> None:
    page = ssrf.fetch_url(
        "https://news.example/a",
        resolver=fake_resolver({"news.example": [PUBLIC_IP]}),
        transport=mock_transport({"https://news.example/a": html_response()}),
    )

    assert page.url == "https://news.example/a"
    assert page.body == HTML


@pytest.mark.parametrize(
    "url",
    [
        "http://news.example/a",
        "ftp://news.example/a",
        "https://user:pw@news.example/a",
        "https://news.example:8443/a",
        "https://localhost/a",
    ],
)
def test_url_shape_is_refused(url: str) -> None:
    with pytest.raises(ssrf.UnsafeURLError):
        ssrf.fetch_url(url, resolver=fake_resolver({}), transport=mock_transport({}))


@pytest.mark.parametrize(
    "url", ["https://127.0.0.1/", "https://[::1]/", "https://169.254.169.254/latest/"]
)
def test_private_ip_literals_are_refused(url: str) -> None:
    with pytest.raises(ssrf.UnsafeURLError):
        ssrf.fetch_url(url, resolver=fake_resolver({}), transport=mock_transport({}))


def test_host_resolving_to_a_private_address_is_refused() -> None:
    resolver = fake_resolver({"intranet.example": [PUBLIC_IP, "10.0.0.5"]})

    with pytest.raises(ssrf.UnsafeURLError):
        ssrf.fetch_url(
            "https://intranet.example/", resolver=resolver, transport=mock_transport({})
        )


def test_redirect_to_a_private_address_is_refused() -> None:
    resolver = fake_resolver(
        {"news.example": [PUBLIC_IP], "internal.example": ["192.168.1.10"]}
    )
    transport = mock_transport(
        {
            "https://news.example/a": httpx.Response(
                302, headers={"location": "https://internal.example/admin"}
            ),
            "https://internal.example/admin": html_response(),
        }
    )

    with pytest.raises(ssrf.UnsafeURLError):
        ssrf.fetch_url("https://news.example/a", resolver=resolver, transport=transport)


def test_more_than_three_redirects_are_refused() -> None:
    resolver = fake_resolver({"news.example": [PUBLIC_IP]})
    routes = {
        f"https://news.example/{i}": httpx.Response(
            301, headers={"location": f"https://news.example/{i + 1}"}
        )
        for i in range(5)
    }

    with pytest.raises(ssrf.FetchError, match="too many"):
        ssrf.fetch_url(
            "https://news.example/0",
            resolver=resolver,
            transport=mock_transport(routes),
        )


def test_response_over_200_kb_is_refused() -> None:
    big = html_response(b"<p>" + b"x" * (ssrf.MAX_BYTES + 10) + b"</p>")

    with pytest.raises(ssrf.FetchError, match="200 KB"):
        ssrf.fetch_url(
            "https://news.example/big",
            resolver=fake_resolver({"news.example": [PUBLIC_IP]}),
            transport=mock_transport({"https://news.example/big": big}),
        )


def test_guarded_backend_refuses_private_address_at_connect_time() -> None:
    backend = ssrf.GuardedBackend(fake_resolver({"rebind.example": ["127.0.0.1"]}))

    with pytest.raises(ssrf.UnsafeURLError):
        backend.connect_tcp("rebind.example", 443)


def test_extract_text_returns_headline_and_lead_within_the_cap() -> None:
    body = (
        b"<html><head><title>Big headline</title></head><body><article><h1>Big headline</h1>"
        + b"<p>"
        + b"Lead sentence with enough words to be kept by the extractor. " * 60
        + b"</p></article></body></html>"
    )
    page = ssrf.FetchedPage(
        url="https://news.example/a", body=body, content_type="text/html"
    )

    extracted = ssrf.extract_text(page, max_chars=500)

    assert extracted.title == "Big headline"
    assert extracted.text.startswith("Big headline")
    assert len(extracted.text) <= 500
