"""HTTP client for the Hostinger AI Router (OpenAI-compatible chat completions).

The API key is only sent in the Authorization header. It never appears in logs or
exception messages. Model IDs come from configuration (``CORE_MODELS``).
"""

from __future__ import annotations

import logging
from typing import Self

import httpx

logger = logging.getLogger(__name__)

CALL_TIMEOUT_S = 20.0
MAX_ATTEMPTS = 2  # one retry on timeout or 5xx


class ModelCallError(RuntimeError):
    """A model call failed after retries. The message never contains the key or model text."""


class RouterClient:
    """Synchronous AI Router client, safe to share between worker threads."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = CALL_TIMEOUT_S,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ModelCallError("AI_ROUTER_API_KEY is not set")
        self._client = httpx.Client(
            base_url=base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(timeout),
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def complete(self, model: str, messages: list[dict[str, str]]) -> str:
        """Return the assistant message content for one chat completion.

        Retries once on timeout or a 5xx response. Raises ModelCallError otherwise.
        """
        payload = {"model": model, "messages": messages, "temperature": 0}
        reason = "no attempt made"
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = self._client.post("chat/completions", json=payload)
            except httpx.TimeoutException:
                reason = "timeout"
                logger.warning("model %s: timeout on attempt %d", model, attempt)
                continue
            except httpx.TransportError as exc:
                raise ModelCallError(
                    f"model {model}: transport error {type(exc).__name__}"
                ) from None

            if response.status_code >= 500:
                reason = f"HTTP {response.status_code}"
                logger.warning("model %s: %s on attempt %d", model, reason, attempt)
                continue
            if response.status_code >= 400:
                raise ModelCallError(f"model {model}: HTTP {response.status_code}")
            return _extract_content(model, response)

        raise ModelCallError(
            f"model {model}: failed after {MAX_ATTEMPTS} attempts ({reason})"
        )


def _extract_content(model: str, response: httpx.Response) -> str:
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise ModelCallError(f"model {model}: unexpected response shape") from None
    if not isinstance(content, str):
        raise ModelCallError(f"model {model}: response content is not text")
    return content
