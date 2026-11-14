"""Test helpers for core: a fake AI Router at the httpx transport layer. No network."""

from __future__ import annotations

import json
from collections import Counter

import httpx
from app.config import Settings
from app.core.clients import RouterClient

FAKE_KEY = "test-key-not-real-0042"
MODELS = ["model-a", "model-b", "model-c"]

# Per model: a reply string, an int HTTP status, "timeout", or a list of those per attempt.
Behaviour = str | int | list


def answer(technique: str, spans: list[str], explanation: str = "Because.") -> str:
    return json.dumps(
        {"technique": technique, "evidence_spans": spans, "explanation": explanation}
    )


def live_settings() -> Settings:
    return Settings(
        core_mode="live", ai_router_api_key=FAKE_KEY, core_models=list(MODELS)
    )


class FakeRouter:
    def __init__(self, behaviours: dict[str, Behaviour]) -> None:
        self.behaviours = behaviours
        self.calls: Counter[str] = Counter()

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        assert request.headers["Authorization"] == f"Bearer {FAKE_KEY}"
        model = json.loads(request.content)["model"]
        attempt = self.calls[model]
        self.calls[model] += 1
        behaviour = self.behaviours[model]
        if isinstance(behaviour, list):
            behaviour = behaviour[min(attempt, len(behaviour) - 1)]
        if behaviour == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        if isinstance(behaviour, int):
            return httpx.Response(behaviour, json={"error": "fake"})
        return httpx.Response(
            200, json={"choices": [{"message": {"content": behaviour}}]}
        )

    def client(self) -> RouterClient:
        return RouterClient(
            "https://router.example.test/v1",
            FAKE_KEY,
            transport=httpx.MockTransport(self.handler),
        )
