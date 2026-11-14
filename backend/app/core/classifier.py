"""The ONE classifier of Spot the Spin.

Game, Analyze, /metrics and the news job call ``classify(text) -> Verdict`` and nothing
else. ``CORE_MODE=stub`` (the default) returns deterministic fake verdicts with no
network; ``CORE_MODE=live`` asks each model in ``CORE_MODELS`` through the AI Router in
parallel and aggregates the votes.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.config import Settings, get_settings, require_live_key
from app.core.aggregate import aggregate, validate_spans
from app.core.cache import CACHE_DIR, VerdictCache, cache_key
from app.core.clients import ModelCallError, RouterClient
from app.core.prompt import (
    PROMPT_VERSION,
    FewShotExample,
    build_messages,
    fewshot_hash,
    load_fewshot,
)
from app.core.retrieval import examples_for_text
from app.core.stub import stub_classify
from app.core.types import TECHNIQUES, ModelVote, Verdict

logger = logging.getLogger(__name__)

EXPECTED_MODELS = 3


def classify(text: str) -> Verdict:
    """Classify one text. The single public entry point of the core."""
    _check_text(text)
    settings = get_settings()
    if settings.core_mode == "stub":
        return stub_classify(text)
    return classify_live(text, settings=settings)


def _check_text(text: str) -> None:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be a non-empty string")


def parse_vote(model: str, raw: str, text: str) -> ModelVote | None:
    """Turn one model answer into a ModelVote, or None if it must be discarded.

    Invalid JSON, a wrong shape or an unknown technique discards the vote. Evidence spans
    that are not exact substrings of ``text`` are dropped. Logs never include model text.
    """
    body = raw.strip()
    if body.startswith("```"):
        # Tolerate a Markdown code fence around the JSON; the JSON itself is not edited.
        body = body.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        logger.warning("discarding vote from model %s: invalid JSON", model)
        return None
    if not isinstance(data, dict):
        logger.warning("discarding vote from model %s: JSON is not an object", model)
        return None

    technique = data.get("technique")
    if technique not in TECHNIQUES:
        logger.warning("discarding vote from model %s: unknown technique", model)
        return None
    spans = data.get("evidence_spans", [])
    explanation = data.get("explanation", "")
    if not isinstance(spans, list) or not isinstance(explanation, str):
        logger.warning("discarding vote from model %s: wrong field types", model)
        return None

    kept = [] if technique == "no_technique" else validate_spans(text, spans)
    dropped = len(spans) - len(kept)
    if dropped and technique != "no_technique":
        logger.info(
            "model %s: dropped %d evidence span(s) not found in the text",
            model,
            dropped,
        )
    return ModelVote(
        model=model, technique=technique, evidence_spans=kept, explanation=explanation
    )


def classify_live(
    text: str,
    *,
    settings: Settings,
    client: RouterClient | None = None,
    cache_dir: Path = CACHE_DIR,
    examples: list[FewShotExample] | None = None,
) -> Verdict:
    """Live ensemble classification. ``client``, ``cache_dir`` and ``examples`` are for tests.

    ``examples=None`` (the normal case) selects the few-shot examples for this text from
    ``settings.fewshot_mode`` and ``settings.retrieval_k``.
    """
    _check_text(text)
    require_live_key(settings)
    models = list(dict.fromkeys(settings.core_models))
    if not models:
        raise RuntimeError(
            "CORE_MODE=live requires CORE_MODELS (comma-separated model IDs)"
        )
    if len(models) != EXPECTED_MODELS:
        logger.warning(
            "CORE_MODELS has %d models; the ensemble expects %d",
            len(models),
            EXPECTED_MODELS,
        )

    if examples is None:
        # Per-text examples: fixed anchors plus (in retrieval mode) the nearest dev ones.
        # The cache key below hashes the examples actually used.
        examples = examples_for_text(
            text,
            mode=settings.fewshot_mode,
            k=settings.retrieval_k,
            anchors=load_fewshot(),
        )
    cache = VerdictCache(cache_dir)
    key = cache_key(PROMPT_VERSION, text, models, fewshot_hash(examples))
    cached = cache.get(key)
    if cached is not None and all(span in text for span in cached.evidence_spans):
        return cached

    messages = build_messages(text, examples)
    own_client = client is None
    if own_client:
        client = RouterClient(settings.ai_router_base_url, settings.ai_router_api_key)
    try:
        with ThreadPoolExecutor(max_workers=len(models)) as pool:
            futures = [
                pool.submit(client.complete, model, messages) for model in models
            ]
            answers: list[str | None] = []
            for model, future in zip(models, futures, strict=True):
                try:
                    answers.append(future.result())
                except ModelCallError as exc:
                    logger.warning("excluding model: %s", exc)
                    answers.append(None)
    finally:
        if own_client:
            client.close()

    votes = [
        vote
        for model, raw in zip(models, answers, strict=True)
        if raw is not None and (vote := parse_vote(model, raw, text)) is not None
    ]
    verdict = aggregate(votes, n_models=len(models))

    # A failed call may be transient; do not freeze its effect in the cache.
    if all(raw is not None for raw in answers):
        cache.put(key, verdict, prompt_version=PROMPT_VERSION, models=models)
    return verdict
