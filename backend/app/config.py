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
DEFAULT_DAILY_TZ = "Europe/Helsinki"
VALID_FEWSHOT_MODES = ("retrieval", "static")
DEFAULT_RETRIEVAL_K = 4
MAX_RETRIEVAL_K = 12


@dataclass(frozen=True)
class Settings:
    core_mode: str = "stub"
    ai_router_base_url: str = DEFAULT_ROUTER_BASE_URL
    ai_router_api_key: str = field(default="", repr=False)
    db_path: Path = DEFAULT_DB_PATH
    core_models: list[str] = field(default_factory=list)
    public_domain: str = ""
    # Daily challenge job (see app.scheduler and app.news.daily). Off by default so
    # tests and local development never start it.
    daily_job_enabled: bool = False
    daily_job_hour: int = 0
    daily_job_minute: int = 0
    daily_tz: str = DEFAULT_DAILY_TZ
    # Used only when the IANA data for ``daily_tz`` is missing (slim images): EET.
    daily_tz_fallback_offset_minutes: int = 120
    # Labelling caps (the paid step): clusters per run, headlines per cluster, headlines
    # per run (cluster headlines + lineup pool; the cluster share is split fairly).
    daily_max_clusters: int = 12
    daily_max_per_cluster: int = 8
    daily_max_headlines: int = 200
    # Headlines from the whole in-window list (balanced across outlets) that are labelled
    # as the pool for the "lineup" round (three headlines from three different stories).
    # 0 (the default) disables lineup rounds: rounds are about one event only.
    daily_lineup_pool: int = 0
    # Spend protection: successful /api/analyze calls per local day in ``daily_tz`` across
    # all visitors (each call costs three model requests). 0 means unlimited.
    analyze_daily_cap: int = 500
    # Few-shot examples: "retrieval" = fixed anchors + the nearest labelled dev examples
    # for each text; "static" = the fixed anchors only (the old behaviour, for ablations).
    fewshot_mode: str = "retrieval"
    # Nearest dev examples added after the anchors (0 = anchors only).
    retrieval_k: int = DEFAULT_RETRIEVAL_K


def _parse_list(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _parse_bool(name: str, raw: str, default: bool) -> bool:
    value = raw.strip().lower()
    if not value:
        return default
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{name} must be true or false")


def _parse_int(name: str, raw: str, default: int, low: int, high: int) -> int:
    value = raw.strip()
    if not value:
        return default
    try:
        number = int(value)
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None
    if not low <= number <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return number


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

    fewshot_mode = environ.get("FEWSHOT_MODE", "").strip().lower() or "retrieval"
    if fewshot_mode not in VALID_FEWSHOT_MODES:
        raise ValueError(
            f"FEWSHOT_MODE must be one of {VALID_FEWSHOT_MODES}, got {fewshot_mode!r}"
        )

    return Settings(
        core_mode=core_mode,
        fewshot_mode=fewshot_mode,
        retrieval_k=_parse_int(
            "RETRIEVAL_K",
            environ.get("RETRIEVAL_K", ""),
            DEFAULT_RETRIEVAL_K,
            0,
            MAX_RETRIEVAL_K,
        ),
        ai_router_base_url=environ.get("AI_ROUTER_BASE_URL", DEFAULT_ROUTER_BASE_URL).strip(),
        ai_router_api_key=environ.get("AI_ROUTER_API_KEY", "").strip(),
        db_path=_resolve_db_path(environ.get("DB_PATH", "").strip() or str(DEFAULT_DB_PATH)),
        core_models=_parse_list(environ.get("CORE_MODELS", "")),
        public_domain=environ.get("PUBLIC_DOMAIN", "").strip(),
        daily_job_enabled=_parse_bool(
            "DAILY_JOB_ENABLED", environ.get("DAILY_JOB_ENABLED", ""), False
        ),
        daily_job_hour=_parse_int(
            "DAILY_JOB_HOUR", environ.get("DAILY_JOB_HOUR", ""), 0, 0, 23
        ),
        daily_job_minute=_parse_int(
            "DAILY_JOB_MINUTE", environ.get("DAILY_JOB_MINUTE", ""), 0, 0, 59
        ),
        daily_tz=environ.get("DAILY_TZ", "").strip() or DEFAULT_DAILY_TZ,
        daily_tz_fallback_offset_minutes=_parse_int(
            "DAILY_TZ_FALLBACK_OFFSET_MINUTES",
            environ.get("DAILY_TZ_FALLBACK_OFFSET_MINUTES", ""),
            120,
            -14 * 60,
            14 * 60,
        ),
        daily_max_clusters=_parse_int(
            "DAILY_MAX_CLUSTERS", environ.get("DAILY_MAX_CLUSTERS", ""), 12, 1, 50
        ),
        daily_max_per_cluster=_parse_int(
            "DAILY_MAX_PER_CLUSTER", environ.get("DAILY_MAX_PER_CLUSTER", ""), 8, 3, 50
        ),
        daily_max_headlines=_parse_int(
            "DAILY_MAX_HEADLINES", environ.get("DAILY_MAX_HEADLINES", ""), 200, 3, 500
        ),
        daily_lineup_pool=_parse_int(
            "DAILY_LINEUP_POOL", environ.get("DAILY_LINEUP_POOL", ""), 0, 0, 400
        ),
        analyze_daily_cap=_parse_int(
            "ANALYZE_DAILY_CAP", environ.get("ANALYZE_DAILY_CAP", ""), 500, 0, 1_000_000
        ),
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
