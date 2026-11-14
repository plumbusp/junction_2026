"""Shared request dependencies: app state access and the anonymous player cookie."""

from __future__ import annotations

import re
import secrets

from fastapi import Request, Response

PLAYER_COOKIE = "sts_player"
PLAYER_COOKIE_MAX_AGE = 365 * 24 * 3600
_PLAYER_ID = re.compile(r"[A-Za-z0-9_-]{16,64}")


def player_id(request: Request, response: Response) -> str:
    """Anonymous player id from a random, httponly cookie; issued when missing or malformed.

    No accounts and no personal data: the id only links one browser's answers and
    dispute votes. It is set on the response when FastAPI builds it from the return value.
    """
    raw = request.cookies.get(PLAYER_COOKIE, "")
    if _PLAYER_ID.fullmatch(raw):
        return raw
    new_id = secrets.token_urlsafe(18)
    response.set_cookie(
        PLAYER_COOKIE,
        new_id,
        max_age=PLAYER_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )
    return new_id


def client_ip(request: Request) -> str:
    """Client IP as seen by uvicorn (behind Traefik, from trusted proxy headers)."""
    return request.client.host if request.client else "unknown"
