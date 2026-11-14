"""File cache of live verdicts under ``data/cache/``.

Key = sha256 of (PROMPT_VERSION, few-shot hash, normalized text, sorted model IDs).
The few-shot hash makes a change in ``data/dev/fewshot.jsonl`` invalidate old verdicts
without a version bump. One JSON file per
key. A hit returns the stored verdict unchanged. Stub verdicts are never cached.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import unicodedata
import uuid
from dataclasses import asdict
from pathlib import Path

from app.config import REPO_ROOT
from app.core.types import ModelVote, Verdict

logger = logging.getLogger(__name__)

CACHE_DIR = REPO_ROOT / "data" / "cache"


def normalize_text(text: str) -> str:
    """NFC, collapse whitespace runs to one space, strip. Case is kept."""
    return " ".join(unicodedata.normalize("NFC", text).split())


def cache_key(
    prompt_version: str, text: str, models: list[str], fewshot_hash: str
) -> str:
    """``fewshot_hash`` is the sha256 of the few-shot examples in the prompt."""
    material = json.dumps(
        [prompt_version, fewshot_hash, normalize_text(text), sorted(models)],
        ensure_ascii=False,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _verdict_from_dict(data: dict) -> Verdict:
    votes = [ModelVote(**vote) for vote in data["votes"]]
    return Verdict(**{**data, "votes": votes})


class VerdictCache:
    def __init__(self, directory: Path = CACHE_DIR) -> None:
        self.directory = directory

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> Verdict | None:
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return _verdict_from_dict(data["verdict"])
        except (OSError, ValueError, KeyError, TypeError):
            logger.warning("ignoring unreadable cache entry %s", path.name)
            return None

    def put(
        self, key: str, verdict: Verdict, *, prompt_version: str, models: list[str]
    ) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        record = {
            "prompt_version": prompt_version,
            "models": sorted(models),
            "verdict": asdict(verdict),
        }
        path = self._path(key)
        tmp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        tmp.write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, path)
