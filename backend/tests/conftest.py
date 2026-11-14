"""Shared pytest setup. Tests never make live model calls."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Force stub mode for the whole session unless the caller set CORE_MODE explicitly.
os.environ.setdefault("CORE_MODE", "stub")


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    """A throwaway SQLite path inside pytest's temporary directory."""
    return tmp_path / "test_app.db"
