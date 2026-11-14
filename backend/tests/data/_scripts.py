"""Load scripts/data/*.py as modules (scripts/ is not an installed package)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "scripts" / "data"
FIXTURE_DIR = REPO_ROOT / "backend" / "tests" / "fixtures" / "data"


def load_script(name: str) -> ModuleType:
    module_name = f"scripts_data_{name}"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module  # dataclasses need the module registered
    spec.loader.exec_module(module)
    return module
