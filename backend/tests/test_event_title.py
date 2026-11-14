"""Event title: one model writes a neutral label; keywords are the fallback."""

from __future__ import annotations

from dataclasses import replace

from app.config import load_settings
from app.core.clients import ModelCallError
from app.news.job import event_label, event_title

HEADLINES = ["Huang steps down as Nvidia CEO", "Nvidia names interim chief after Huang exit"]
TERMS = ["huang", "nvidia", "ceo"]


class FakeClient:
    def __init__(self, answer: str | Exception) -> None:
        self.answer = answer
        self.calls: list[tuple[str, list]] = []

    def complete(self, model, messages):
        self.calls.append((model, messages))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def live_settings():
    return replace(load_settings({"CORE_MODE": "stub"}), core_mode="live", core_models=["m1", "m2"])


def test_stub_mode_uses_keywords_without_a_call():
    client = FakeClient("Nvidia CEO steps down")
    assert event_title(HEADLINES, TERMS, load_settings({"CORE_MODE": "stub"}), client) == event_label(TERMS)
    assert client.calls == []


def test_live_label_is_cleaned_and_uses_all_headlines():
    client = FakeClient('  "Nvidia CEO Jensen Huang steps down."\n')
    assert event_title(HEADLINES, TERMS, live_settings(), client) == "Nvidia CEO Jensen Huang steps down"
    model, messages = client.calls[0]
    assert model == "m1"
    assert all(h in messages[0]["content"] for h in HEADLINES)


def test_model_error_or_bad_answer_falls_back():
    assert event_title(HEADLINES, TERMS, live_settings(), FakeClient(ModelCallError("timeout"))) == event_label(TERMS)
    assert event_title(HEADLINES, TERMS, live_settings(), FakeClient("word")) == event_label(TERMS)
    assert event_title(HEADLINES, TERMS, live_settings(), FakeClient("x " * 60)) == event_label(TERMS)
