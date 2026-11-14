"""Typed settings loaded from environment variables and the repo-root .env.

Secret values are never printed: ``ai_router_api_key`` is excluded from ``repr``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"
DEFAULT_ROUTER_BASE_URL = "https://router.hostinger.com/v1"
DEFAULT_DB_PATH = REPO_ROOT / "data" / "app.db"
VALID_CORE_MODES = ("stub", "live")


@dataclass(frozen=True)
class Settings:
    core_mode: str = "stub"
    ai_router_base_url: str = DEFAULT_ROUTER_BASE_URL
    ai_router_api_key: str = field(default="", repr=False)
    db_path: Path = DEFAULT_DB_PATH
    core_models: list[str] = field(default_factory=list)
    public_domain: str = ""


def _parse_list(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _resolve_db_path(raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else REPO_ROOT / path


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Build Settings from ``environ``.

    When ``environ`` is None, the repo-root .env is loaded (existing environment
    variables win) and ``os.environ`` is used. Passing a mapping skips .env loading.
    """
    if environ is None:
        load_dotenv(ENV_FILE, override=False)
        environ = os.environ

    core_mode = environ.get("CORE_MODE", "stub").strip().lower()
    if core_mode not in VALID_CORE_MODES:
        raise ValueError(f"CORE_MODE must be one of {VALID_CORE_MODES}, got {core_mode!r}")

    return Settings(
        core_mode=core_mode,
        ai_router_base_url=environ.get("AI_ROUTER_BASE_URL", DEFAULT_ROUTER_BASE_URL).strip(),
        ai_router_api_key=environ.get("AI_ROUTER_API_KEY", "").strip(),
        db_path=_resolve_db_path(environ.get("DB_PATH", "").strip() or str(DEFAULT_DB_PATH)),
        core_models=_parse_list(environ.get("CORE_MODELS", "")),
        public_domain=environ.get("PUBLIC_DOMAIN", "").strip(),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide cached Settings."""
    return load_settings()


def require_live_key(settings: Settings | None = None) -> None:
    """Raise RuntimeError if live mode is requested without an API key.

    Only the variable name is mentioned in the message, never the value.
    """
    settings = settings or get_settings()
    if settings.core_mode == "live" and not settings.ai_router_api_key:
        raise RuntimeError(
            "CORE_MODE=live requires AI_ROUTER_API_KEY to be set in the environment or .env"
        )
