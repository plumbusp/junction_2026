"""Error handling: every error is a short JSON body, never a stack trace.

- ``ApiError`` carries a status, a machine-readable ``error`` code and a user-facing
  ``detail`` (the frontend shows ``detail``).
- Validation errors become 422 with ``detail`` as a list of ``{loc, msg}`` (no echo of
  the submitted input).
- ``SafetyMiddleware`` catches anything unhandled, logs only the exception type and the
  route (no message, no user text, no secrets) and answers 500 JSON. It also adds a few
  security headers to every response.
- ``BodySizeLimitMiddleware`` refuses request bodies over ``MAX_BODY_BYTES`` with 413.
"""

from __future__ import annotations

import json
import logging
import traceback

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger("app.api")

MAX_BODY_BYTES = 64 * 1024

SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"x-frame-options", b"DENY"),
]


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        error: str,
        detail: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(error)
        self.status_code = status_code
        self.error = error
        self.detail = detail
        self.headers = headers or {}


def error_body(error: str, detail: object) -> dict:
    return {"error": error, "detail": detail}


async def _api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        error_body(exc.error, exc.detail),
        status_code=exc.status_code,
        headers=exc.headers,
    )


async def _validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        {
            "loc": [str(part) for part in err.get("loc", ())],
            "msg": str(err.get("msg", "")),
        }
        for err in exc.errors()
    ]
    return JSONResponse(error_body("invalid_request", details), status_code=422)


async def _http_error_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = {404: "not_found", 405: "method_not_allowed"}.get(
        exc.status_code, "http_error"
    )
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed."
    return JSONResponse(
        error_body(code, detail),
        status_code=exc.status_code,
        headers=getattr(exc, "headers", None),
    )


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)


async def _send_json(send: Send, status: int, body: dict) -> None:
    payload = json.dumps(body).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode("ascii")),
                *SECURITY_HEADERS,
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})


class SafetyMiddleware:
    """Outermost app middleware: security headers and a last-resort 500 JSON."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                headers.extend(h for h in SECURITY_HEADERS if h[0] not in present)
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:  # noqa: BLE001 - last resort, never re-raised
            frames = "".join(traceback.format_tb(exc.__traceback__))
            log.error(
                "unhandled %s on %s %s\n%s",
                type(exc).__name__,
                scope.get("method"),
                scope.get("path"),
                frames,
            )
            if not started:
                await _send_json(
                    send, 500, error_body("internal_error", "Internal server error.")
                )


class BodySizeLimitMiddleware:
    """Refuse request bodies larger than ``max_bytes`` with 413 JSON."""

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS"):
            await self.app(scope, receive, send)
            return
        too_large = error_body("payload_too_large", "The request body is too large.")
        headers = dict(scope.get("headers", []))
        declared = headers.get(b"content-length")
        if declared is not None:
            try:
                size = int(declared)
            except ValueError:
                size = self.max_bytes + 1
            if size > self.max_bytes:
                await _send_json(send, 413, too_large)
                return
            await self.app(scope, receive, send)
            return

        # No Content-Length (chunked): buffer up to the limit, then replay.
        body = b""
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body += message.get("body", b"")
            more = message.get("more_body", False)
            if len(body) > self.max_bytes:
                await _send_json(send, 413, too_large)
                return
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
