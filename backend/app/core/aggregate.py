"""Pure aggregation of model votes into a Verdict. No I/O, no logging."""

from __future__ import annotations

from collections import Counter

from app.core.types import ModelVote, Verdict

NO_VOTES_EXPLANATION = (
    "No model returned a usable answer, so there is no verdict for this text."
)
DISAGREEMENT_EXPLANATION = (
    "The models disagreed about this text, so no confident verdict is given."
)
GENERIC_EXPLANATION = "Most models agreed on this technique but gave no explanation."


def validate_spans(text: str, spans: list[str]) -> list[str]:
    """Keep only non-empty spans that are exact substrings of ``text``, deduplicated in order.

    Spans are never edited: a span that does not match exactly is dropped.
    """
    kept: list[str] = []
    for span in spans:
        if isinstance(span, str) and span and span in text and span not in kept:
            kept.append(span)
    return kept


def aggregate(votes: list[ModelVote], n_models: int) -> Verdict:
    """Combine successful votes into one Verdict.

    - technique: the label with the most votes, if it has at least 2 votes and no tie;
      otherwise ``no_technique`` with ``disputed=True``;
    - evidence_spans: spans chosen by at least 2 of the models that voted for the final
      technique (empty when there is no majority);
    - explanation: from a model in the majority, or a neutral sentence on disagreement;
    - agreement: number of votes for the final technique.
    """
    if not votes:
        return Verdict(
            technique="no_technique",
            evidence_spans=[],
            explanation=NO_VOTES_EXPLANATION,
            agreement=0,
            n_models=n_models,
            disputed=True,
            votes=[],
        )

    counts = Counter(vote.technique for vote in votes)
    ranked = counts.most_common()
    top_label, top_count = ranked[0]
    tied = len(ranked) > 1 and ranked[1][1] == top_count

    if top_count < 2 or tied:
        return Verdict(
            technique="no_technique",
            evidence_spans=[],
            explanation=DISAGREEMENT_EXPLANATION,
            agreement=counts.get("no_technique", 0),
            n_models=n_models,
            disputed=True,
            votes=list(votes),
        )

    majority = [vote for vote in votes if vote.technique == top_label]
    span_counts: Counter[str] = Counter()
    ordered_spans: list[str] = []
    for vote in majority:
        for span in dict.fromkeys(vote.evidence_spans):  # one count per model
            span_counts[span] += 1
            if span not in ordered_spans:
                ordered_spans.append(span)
    spans = [span for span in ordered_spans if span_counts[span] >= 2]

    explanation = next(
        (vote.explanation.strip() for vote in majority if vote.explanation.strip()),
        GENERIC_EXPLANATION,
    )

    return Verdict(
        technique=top_label,
        evidence_spans=spans,
        explanation=explanation,
        agreement=top_count,
        n_models=n_models,
        disputed=False,
        votes=list(votes),
    )
