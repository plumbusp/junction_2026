"""Group headlines about the same event.

Method: TF-IDF vectors over headline tokens (pure Python), cosine similarity,
then average-linkage agglomerative clustering. Two clusters merge only when
their average pairwise similarity reaches ``threshold`` and the merged cluster
spans at most ``window_hours``. A cluster becomes a *candidate* only when its
headlines come from at least ``min_outlets`` distinct outlets.

Average linkage (rather than single linkage) keeps one shared generic word from
chaining unrelated stories together.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.news.rss import NewsItem

# Chosen on the fixture feeds in backend/tests/fixtures/rss (18 in-window
# headlines, small and invented, so this is a starting point, not a tuned value):
# same-event pairs score 0.235-0.494; cross-event pairs score <= 0.153, except one
# deliberately hard negative ("Government ... plan" x2, 0.313) which merges into a
# 2-outlet pair and is dropped by the >= 3 outlets rule. 0.20 sits in the gap.
# With average linkage a headline joins only if it is, on average, that close to
# every member already in the cluster. Re-check on real feeds.
DEFAULT_THRESHOLD = 0.20
DEFAULT_WINDOW_HOURS = 48
DEFAULT_MIN_OUTLETS = 3
FUTURE_SKEW = timedelta(hours=1)

STOPWORDS = frozenset(
    [
        "a",
        "about",
        "after",
        "again",
        "against",
        "all",
        "also",
        "am",
        "amid",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "been",
        "before",
        "being",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "down",
        "during",
        "each",
        "for",
        "from",
        "further",
        "had",
        "has",
        "have",
        "having",
        "he",
        "her",
        "here",
        "hers",
        "him",
        "his",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "just",
        "me",
        "more",
        "most",
        "my",
        "new",
        "no",
        "nor",
        "not",
        "now",
        "of",
        "off",
        "on",
        "once",
        "only",
        "or",
        "other",
        "our",
        "out",
        "over",
        "own",
        "s",
        "same",
        "she",
        "should",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "to",
        "too",
        "under",
        "until",
        "up",
        "very",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "says",
        "say",
        "said",
        "told",
        "tells",
        "report",
        "reports",
        "reported",
        "live",
        "update",
        "updates",
        "latest",
        "watch",
        "video",
        "news",
        "breaking",
        "analysis",
        "opinion",
        "first",
        "get",
        "gets",
        "got",
        "make",
        "makes",
        "made",
        "take",
        "takes",
        "year",
        "years",
        "day",
        "days",
        "week",
        "weeks",
    ]
)

_TOKEN = re.compile(r"[a-z0-9]+(?:[.'][a-z0-9]+)*")


def _normalise(token: str) -> str:
    token = token.replace("'", "")
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith(("sses", "ches", "shes", "xes")) and len(token) > 4:
        return token[:-2]
    if (
        token.endswith("s")
        and not token.endswith(("ss", "us", "is"))
        and len(token) > 3
    ):
        return token[:-1]
    return token


def tokenize(title: str) -> list[str]:
    """Lowercase content tokens with possessives and simple plurals removed."""
    tokens = []
    for raw in _TOKEN.findall(title.lower().replace("’", "'")):
        raw = raw.removesuffix("'s")
        token = _normalise(raw)
        if len(token) < 2 or token in STOPWORDS:
            continue
        tokens.append(token)
    return tokens


def tfidf_vectors(docs: Sequence[Sequence[str]]) -> list[dict[str, float]]:
    """L2-normalised TF-IDF vectors with smoothed idf: log((1+N)/(1+df)) + 1."""
    n_docs = len(docs)
    df: Counter[str] = Counter()
    for doc in docs:
        df.update(set(doc))
    vectors = []
    for doc in docs:
        tf = Counter(doc)
        vec = {
            t: c * (math.log((1 + n_docs) / (1 + df[t])) + 1.0) for t, c in tf.items()
        }
        norm = math.sqrt(sum(w * w for w in vec.values()))
        vectors.append({t: w / norm for t, w in vec.items()} if norm else {})
    return vectors


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    """Dot product of two L2-normalised sparse vectors."""
    if len(a) > len(b):
        a, b = b, a
    return sum(w * b.get(t, 0.0) for t, w in a.items())


def pairwise_similarities(
    vectors: Sequence[dict[str, float]],
) -> dict[tuple[int, int], float]:
    """Non-zero cosine similarities for pairs (i < j), via an inverted index."""
    postings: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for i, vec in enumerate(vectors):
        for term, weight in vec.items():
            postings[term].append((i, weight))
    sims: dict[tuple[int, int], float] = defaultdict(float)
    for entries in postings.values():
        for x in range(len(entries)):
            i, wi = entries[x]
            for y in range(x + 1, len(entries)):
                j, wj = entries[y]
                sims[(i, j) if i < j else (j, i)] += wi * wj
    return dict(sims)


@dataclass
class Cluster:
    items: list[NewsItem]
    cohesion: float  # mean pairwise cosine similarity inside the cluster (1.0 for a singleton)
    top_terms: list[str]

    @property
    def outlets(self) -> list[str]:
        return sorted({item.outlet for item in self.items})

    @property
    def n_outlets(self) -> int:
        return len(self.outlets)

    @property
    def first_published(self) -> datetime:
        return min(item.published for item in self.items)

    @property
    def last_published(self) -> datetime:
        return max(item.published for item in self.items)

    @property
    def cluster_id(self) -> str:
        """Stable id derived from the member links, so a cluster is traceable across runs."""
        digest = hashlib.sha1(
            "\n".join(sorted(i.link for i in self.items)).encode("utf-8")
        )
        return digest.hexdigest()[:12]


def filter_window(
    items: Sequence[NewsItem], now: datetime, window_hours: int = DEFAULT_WINDOW_HOURS
) -> list[NewsItem]:
    """Items published within ``window_hours`` before ``now`` (small future skew allowed)."""
    start = now - timedelta(hours=window_hours)
    return [item for item in items if start <= item.published <= now + FUTURE_SKEW]


def cluster_items(
    items: Sequence[NewsItem],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    window_hours: int = DEFAULT_WINDOW_HOURS,
) -> list[Cluster]:
    """Average-linkage clustering of headlines. Returns every cluster, singletons included."""
    if not 0.0 < threshold <= 1.0:
        raise ValueError(f"threshold must be in (0, 1], got {threshold}")
    if window_hours <= 0:
        raise ValueError(f"window_hours must be positive, got {window_hours}")

    ordered = sorted(items, key=lambda i: (i.published, i.outlet, i.link))
    if not ordered:
        return []
    token_lists = [tokenize(item.title) for item in ordered]
    vectors = tfidf_vectors(token_lists)
    sims = pairwise_similarities(vectors)
    window = timedelta(hours=window_hours)

    members: dict[int, list[int]] = {i: [i] for i in range(len(ordered))}
    internal: dict[int, float] = {i: 0.0 for i in members}
    span: dict[int, tuple[datetime, datetime]] = {
        i: (it.published, it.published) for i, it in enumerate(ordered)
    }
    links: dict[int, dict[int, float]] = defaultdict(
        dict
    )  # cluster -> neighbour -> summed similarity
    for (i, j), s in sims.items():
        links[i][j] = s
        links[j][i] = s
    next_id = len(ordered)

    while True:
        best: tuple[float, int, int] | None = None
        for a in sorted(links):
            for b, total in links[a].items():
                if b <= a:
                    continue
                avg = total / (len(members[a]) * len(members[b]))
                if avg < threshold or (best is not None and avg <= best[0]):
                    continue
                lo = min(span[a][0], span[b][0])
                hi = max(span[a][1], span[b][1])
                if hi - lo > window:
                    continue
                best = (avg, a, b)
        if best is None:
            break
        _, a, b = best
        c = next_id
        next_id += 1
        cross = links[a].get(b, 0.0)
        members[c] = members.pop(a) + members.pop(b)
        internal[c] = internal.pop(a) + internal.pop(b) + cross
        span[c] = (min(span[a][0], span[b][0]), max(span[a][1], span[b][1]))
        del span[a], span[b]
        merged: dict[int, float] = defaultdict(float)
        for old in (a, b):
            for other, total in links.pop(old, {}).items():
                if other in (a, b):
                    continue
                merged[other] += total
                links[other].pop(old, None)
        for other, total in merged.items():
            links[other][c] = total
        if merged:
            links[c] = dict(merged)

    clusters = []
    for cid, idxs in members.items():
        n = len(idxs)
        cohesion = 1.0 if n == 1 else internal[cid] / (n * (n - 1) / 2)
        weights: Counter[str] = Counter()
        for i in idxs:
            weights.update(vectors[i])
        top_terms = [
            t for t, _ in sorted(weights.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
        ]
        clusters.append(
            Cluster(
                items=[ordered[i] for i in sorted(idxs)],
                cohesion=round(cohesion, 4),
                top_terms=top_terms,
            )
        )
    clusters.sort(key=lambda c: (-c.n_outlets, -c.cohesion, c.cluster_id))
    return clusters


def candidate_clusters(
    items: Sequence[NewsItem],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    min_outlets: int = DEFAULT_MIN_OUTLETS,
) -> list[Cluster]:
    """Clusters whose headlines come from at least ``min_outlets`` distinct outlets."""
    if min_outlets < 1:
        raise ValueError(f"min_outlets must be at least 1, got {min_outlets}")
    clusters = cluster_items(items, threshold=threshold, window_hours=window_hours)
    return [c for c in clusters if c.n_outlets >= min_outlets]
