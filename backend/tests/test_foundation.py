"""Foundation tests: config defaults, secret hiding, core contract validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import DEFAULT_ROUTER_BASE_URL, REPO_ROOT, load_settings, require_live_key
from app.core.types import ModelVote

FAKE_KEY = "test-key-not-real-0001"


def test_config_defaults_are_stub_and_empty() -> None:
    settings = load_settings({})

    assert settings.core_mode == "stub"
    assert settings.ai_router_base_url == DEFAULT_ROUTER_BASE_URL == "https://router.hostinger.com/v1"
    assert settings.ai_router_api_key == ""
    assert settings.db_path == REPO_ROOT / "data" / "app.db"
    assert settings.core_models == []
    assert settings.public_domain == ""


def test_api_key_is_hidden_in_repr_and_str(tmp_db_path: Path) -> None:
    settings = load_settings({"AI_ROUTER_API_KEY": FAKE_KEY, "DB_PATH": str(tmp_db_path)})

    assert settings.ai_router_api_key == FAKE_KEY
    assert FAKE_KEY not in repr(settings)
    assert FAKE_KEY not in str(settings)
    assert settings.db_path == tmp_db_path


def test_live_mode_without_key_raises() -> None:
    settings = load_settings({"CORE_MODE": "live"})

    with pytest.raises(RuntimeError, match="AI_ROUTER_API_KEY"):
        require_live_key(settings)


def test_unknown_technique_raises_value_error() -> None:
    with pytest.raises(ValueError, match="unknown technique"):
        ModelVote(model="claude", technique="not_a_technique", evidence_spans=[], explanation="")
