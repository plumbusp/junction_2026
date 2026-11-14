"""SSRF-safe fetching of a user-supplied URL for Analyze.

Rules (docs: .claude/agents/api-backend.md, Requirements 1):
- only ``https``, default port only, no credentials in the URL;
- every address the host resolves to must be public: private, loopback, link-local,
  reserved, multicast, unspecified and shared (CGNAT) ranges are refused;
- redirects are followed by hand, at most ``MAX_REDIRECTS`` hops, and every hop is
  checked again before it is requested;
- the connection itself goes through ``GuardedBackend``, which resolves and checks the
  host at connect time and connects to the checked IP, so a DNS answer that changes
  between the check and the connection (DNS rebinding) cannot reach a private address;
- response body cap ``MAX_BYTES`` (200 KB), overall timeout ``TIMEOUT_SECONDS`` (10 s).

Errors carry a short user-facing message only. Nothing here logs the URL path, the
query or the page content.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpcore
import httpx
import trafilatura

log = logging.getLogger(__name__)

MAX_REDIRECTS = 3
MAX_BYTES = 200 * 1024
TIMEOUT_SECONDS = 10.0
MAX_URL_LENGTH = 2048
USER_AGENT = "spot-the-spin/0.1 (+analyze)"
ALLOWED_CONTENT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

Resolver = Callable[[str, int], list[str]]


class FetchError(Exception):
    """A URL could not be fetched. ``str(exc)`` is safe to show to the user."""

    status_code = 422
    code = "fetch_failed"


class UnsafeURLError(FetchError):
    """The URL (or a redirect target) is not allowed."""

    status_code = 400
    code = "unsafe_url"


def system_resolver(host: str, port: int) -> list[str]:
    """Resolve ``host`` to a list of IP address strings with the system resolver."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise FetchError("The host name of this URL could not be resolved.") from exc
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def is_public_ip(address: str) -> bool:
    """True only for globally routable unicast addresses."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or not ip.is_global
    )


def check_url(url: str) -> tuple[str, int]:
    """Validate the shape of ``url``; return (host, port). Does not resolve DNS."""
    if not isinstance(url, str) or not url.strip():
        raise UnsafeURLError("Enter a URL starting with https://")
    if len(url) > MAX_URL_LENGTH:
        raise UnsafeURLError("The URL is too long.")
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as exc:
        raise UnsafeURLError("The URL is not valid.") from exc
    if parts.scheme.lower() != "https":
        raise UnsafeURLError("Only https:// URLs are supported.")
    if parts.username is not None or parts.password is not None:
        raise UnsafeURLError("URLs with a user name or password are not supported.")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise UnsafeURLError("The URL has no host name.")
    if port not in (None, 443):
        raise UnsafeURLError("Only the standard https port (443) is supported.")
    lowered = host.lower()
    if lowered == "localhost" or lowered.endswith(".localhost"):
        raise UnsafeURLError("This address is not allowed.")
    return host, 443


def resolve_public(
    host: str, port: int, resolver: Resolver = system_resolver
) -> list[str]:
    """Resolve ``host``; refuse it unless every address is public. Returns the addresses."""
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    addresses = [str(literal)] if literal is not None else resolver(host, port)
    if not addresses:
        raise FetchError("The host name of this URL could not be resolved.")
    if not all(is_public_ip(a) for a in addresses):
        raise UnsafeURLError("This address is not allowed.")
    return addresses


class GuardedBackend(httpcore.SyncBackend):
    """httpcore network backend that checks the resolved IP at connect time.

    It connects to the checked IP itself; TLS still uses the original host name for
    SNI and certificate verification (httpcore passes it to ``start_tls``).
    """

    def __init__(self, resolver: Resolver = system_resolver) -> None:
        self._resolver = resolver

    def connect_tcp(  # type: ignore[override]
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable | None = None,
    ) -> httpcore.NetworkStream:
        addresses = resolve_public(host, port, self._resolver)
        return super().connect_tcp(
            addresses[0],
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )


class GuardedTransport(httpx.HTTPTransport):
    """httpx transport whose connection pool uses ``GuardedBackend``. No proxies."""

    def __init__(self, resolver: Resolver = system_resolver) -> None:
        super().__init__(trust_env=False, retries=0)
        # httpx 0.28 does not expose the network backend; replace the pool it built.
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpx.create_ssl_context(trust_env=False),
            max_connections=4,
            max_keepalive_connections=0,
            network_backend=GuardedBackend(resolver),
        )


@dataclass(frozen=True)
class FetchedPage:
    url: str  # final URL after redirects
    body: bytes
    content_type: str


def _read_capped(response: httpx.Response, deadline: float) -> bytes:
    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BYTES:
        raise FetchError("The page is larger than 200 KB.")
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes():
        size += len(chunk)
        if size > MAX_BYTES:
            raise FetchError("The page is larger than 200 KB.")
        if time.monotonic() > deadline:
            raise FetchError("The page took too long to load.")
        chunks.append(chunk)
    return b"".join(chunks)


def fetch_url(
    url: str,
    *,
    resolver: Resolver = system_resolver,
    transport: httpx.BaseTransport | None = None,
) -> FetchedPage:
    """Fetch ``url`` under the SSRF rules. ``transport`` is for tests."""
    deadline = time.monotonic() + TIMEOUT_SECONDS
    client = httpx.Client(
        transport=transport or GuardedTransport(resolver),
        follow_redirects=False,
        trust_env=False,
        timeout=httpx.Timeout(TIMEOUT_SECONDS),
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
    )
    current = url.strip() if isinstance(url, str) else url
    try:
        for hop in range(MAX_REDIRECTS + 1):
            host, port = check_url(current)
            resolve_public(host, port, resolver)
            if time.monotonic() > deadline:
                raise FetchError("The page took too long to load.")
            try:
                with client.stream("GET", current) as response:
                    if response.status_code in REDIRECT_STATUSES:
                        location = response.headers.get("location")
                        if not location:
                            raise FetchError("The page redirected without a target.")
                        if hop == MAX_REDIRECTS:
                            raise FetchError("The page redirected too many times.")
                        current = urljoin(current, location)
                        continue
                    if response.status_code != 200:
                        raise FetchError(
                            f"The page answered with HTTP {response.status_code}."
                        )
                    content_type = (
                        response.headers.get("content-type", "")
                        .split(";", 1)[0]
                        .strip()
                        .lower()
                    )
                    if content_type not in ALLOWED_CONTENT_TYPES:
                        raise FetchError("The URL does not point to an HTML page.")
                    body = _read_capped(response, deadline)
                    return FetchedPage(
                        url=current, body=body, content_type=content_type
                    )
            except httpx.TimeoutException as exc:
                raise FetchError("The page took too long to load.") from exc
            except httpx.HTTPError as exc:
                log.info("url fetch failed: %s", type(exc).__name__)
                raise FetchError("The page could not be downloaded.") from exc
        raise FetchError("The page redirected too many times.")  # pragma: no cover
    finally:
        client.close()


@dataclass(frozen=True)
class ExtractedText:
    text: str
    title: str | None


def extract_text(page: FetchedPage, max_chars: int) -> ExtractedText:
    """Headline and lead of an article page, at most ``max_chars`` characters."""
    if page.content_type == "text/plain":
        text = page.body.decode("utf-8", errors="replace").strip()
        title = None
    else:
        html = page.body.decode("utf-8", errors="replace")
        doc = trafilatura.bare_extraction(
            html,
            url=page.url,
            with_metadata=True,
            include_comments=False,
            include_tables=False,
        )
        body = (getattr(doc, "text", None) or "").strip()
        title = (getattr(doc, "title", None) or "").strip() or None
        if title and body and not body.startswith(title):
            text = f"{title}\n{body}"
        else:
            text = body or (title or "")
    text = _truncate(text, max_chars)
    if not text.strip():
        raise FetchError("No article text could be extracted from this page.")
    return ExtractedText(text=text, title=title)


def _truncate(text: str, max_chars: int) -> str:
    """Cut at a line or sentence end before ``max_chars`` when possible, else at a space."""
    if len(text) <= max_chars:
        return text
    head = text[:max_chars]
    for sep in ("\n", ". ", "! ", "? "):
        cut = head.rfind(sep)
        if cut >= max_chars // 2:
            return head[: cut + (1 if sep != "\n" else 0)].rstrip()
    cut = head.rfind(" ")
    return (head[:cut] if cut > 0 else head).rstrip()
