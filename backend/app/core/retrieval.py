"""Retrieval of few-shot examples from the labelled dev data.

For each input text the prompt gets the fixed anchors followed by the ``k`` labelled
``data/dev/`` examples most similar to the text. Similarity is a pure-Python TF-IDF
cosine over lowercased word unigrams and bigrams (no dependency). Everything is
deterministic: equal scores are ordered by example id.

The index is built lazily, once per process, from every ``data/dev/*.jsonl`` record that
has a ``technique`` field (``semeval21_dev_multi.jsonl`` has ``techniques`` instead and
contributes nothing). The frozen eval set under ``data/eval/`` is refused by path and is
never read. Tuning and selection of this method happen on dev only.
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

from app.config import MAX_RETRIEVAL_K, REPO_ROOT
from app.core.cache import normalize_text
from app.core.prompt import FewShotExample, _parse_example, refuse_eval_path

logger = logging.getLogger(__name__)

DEV_DIR = REPO_ROOT / "data" / "dev"

# Added to the cosine similarity of news-headline examples (source starting with
# "headlines") so that they win over meme texts when the similarity is close. Cosine
# scores between short texts mostly sit in 0.05-0.40 and neighbours are typically a few
# hundredths apart, so 0.08 breaks near-ties in favour of the product's own domain but
# cannot lift a headline with little word overlap above a clearly closer meme (a gap of
# 0.08 or more). It is a plain constant: tune it on dev with backend/eval/dev_check.py.
HEADLINE_BOOST = 0.08
HEADLINE_SOURCE_PREFIX = "headlines"

_TOKEN = re.compile(r"[^\W_]+")


def _features(text: str) -> Counter[str]:
    """Lowercased word unigrams plus bigrams of adjacent words."""
    tokens = _TOKEN.findall(text.lower())
    counts: Counter[str] = Counter(tokens)
    counts.update(f"{a} {b}" for a, b in pairwise(tokens))
    return counts


def _key(text: str) -> str:
    """Normalized form used to compare texts (input vs candidates vs anchors)."""
    return normalize_text(text).casefold()


@dataclass(frozen=True)
class _Doc:
    id: str
    source: str
    key: str
    example: FewShotExample


class RetrievalIndex:
    """TF-IDF index over labelled examples. Immutable after construction."""

    def __init__(self, docs: Sequence[_Doc]) -> None:
        self._docs = list(docs)
        n = len(self._docs)
        feats = [_features(d.example.text) for d in self._docs]
        df: Counter[str] = Counter()
        for f in feats:
            df.update(f.keys())
        self._idf = {t: math.log((1 + n) / (1 + c)) + 1.0 for t, c in df.items()}
        # A query term unseen in the corpus gets the idf of a term with df = 0.
        self._unseen_idf = math.log(1 + n) + 1.0
        self._postings: dict[str, list[tuple[int, float]]] = defaultdict(list)
        for i, f in enumerate(feats):
            weights = {t: (1 + math.log(c)) * self._idf[t] for t, c in f.items()}
            norm = math.sqrt(sum(w * w for w in weights.values()))
            for t, w in weights.items():
                self._postings[t].append((i, w / norm))

    def __len__(self) -> int:
        return len(self._docs)

    def search(
        self, text: str, *, headline_boost: float = HEADLINE_BOOST
    ) -> list[tuple[float, _Doc]]:
        """Examples sharing at least one feature with ``text``, best first (ties by id)."""
        query = _features(text)
        weights = {
            t: (1 + math.log(c)) * self._idf.get(t, self._unseen_idf)
            for t, c in query.items()
        }
        norm = math.sqrt(sum(w * w for w in weights.values()))
        if not norm:
            return []
        scores: dict[int, float] = defaultdict(float)
        for t, w in weights.items():
            for i, dw in self._postings.get(t, ()):
                scores[i] += (w / norm) * dw
        ranked = []
        for i, cosine in scores.items():
            doc = self._docs[i]
            boost = headline_boost if doc.source.startswith(HEADLINE_SOURCE_PREFIX) else 0.0
            ranked.append((round(cosine + boost, 12), doc))
        ranked.sort(key=lambda pair: (-pair[0], pair[1].id))
        return ranked


def build_index(dev_dir: Path = DEV_DIR) -> RetrievalIndex:
    """Index every ``*.jsonl`` record under ``dev_dir`` that has a valid ``technique``.

    A missing directory gives an empty index. Invalid lines are skipped and logged by
    file name and line number only. Paths inside ``data/eval/`` are refused.
    """
    refuse_eval_path(dev_dir)
    docs: list[_Doc] = []
    seen: set[str] = set()
    paths = sorted(dev_dir.glob("*.jsonl")) if dev_dir.is_dir() else []
    for path in paths:
        refuse_eval_path(path)
        with path.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    record = None
                if not isinstance(record, dict) or "technique" not in record:
                    continue  # e.g. the multi-label file; not an error
                example = _parse_example(line)
                if example is None:
                    logger.warning("skipping invalid line %d in %s", line_no, path.name)
                    continue
                key = _key(example.text)
                if key in seen:
                    continue
                seen.add(key)
                doc_id = str(record.get("id") or f"{path.stem}:{line_no}")
                source = str(record.get("source") or path.stem)
                docs.append(_Doc(doc_id, source, key, example))
    return RetrievalIndex(docs)


_index: RetrievalIndex | None = None
_index_lock = threading.Lock()


def get_index() -> RetrievalIndex:
    """The process-wide index over ``data/dev/``, built on first use."""
    global _index
    with _index_lock:
        if _index is None:
            _index = build_index(DEV_DIR)
        return _index


def set_index(index: RetrievalIndex | None) -> None:
    """Replace the process-wide index (``None`` = rebuild on next use). For tests and tools."""
    global _index
    with _index_lock:
        _index = index


def select_examples(
    text: str,
    *,
    k: int,
    anchors: Sequence[FewShotExample],
    index: RetrievalIndex | None = None,
    headline_boost: float = HEADLINE_BOOST,
) -> list[FewShotExample]:
    """Anchors (deduplicated, in the given order) then the ``k`` nearest dev examples.

    Any example whose normalized text equals ``text`` is skipped, anchors included, so the
    answer never appears in its own prompt (leave-one-out). Retrieved examples never
    repeat an anchor. ``k=0`` gives the anchors only and does not touch the index.
    """
    if not 0 <= k <= MAX_RETRIEVAL_K:
        raise ValueError(f"k must be between 0 and {MAX_RETRIEVAL_K}")
    seen = {_key(text)}
    chosen: list[FewShotExample] = []
    for anchor in anchors:
        key = _key(anchor.text)
        if key not in seen:
            seen.add(key)
            chosen.append(anchor)
    if k == 0:
        return chosen

    if index is None:
        index = get_index()
    taken = 0
    for _score, doc in index.search(text, headline_boost=headline_boost):
        if taken == k:
            break
        if doc.key in seen:
            continue
        seen.add(doc.key)
        chosen.append(doc.example)
        taken += 1
    return chosen


def examples_for_text(
    text: str,
    *,
    mode: str,
    k: int,
    anchors: Sequence[FewShotExample],
    index: RetrievalIndex | None = None,
) -> list[FewShotExample]:
    """The example list for one input under FEWSHOT_MODE: "retrieval" or "static"."""
    if mode == "static":
        k = 0
    return select_examples(text, k=k, anchors=anchors, index=index)
