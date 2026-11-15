"""Metrics on a 12-item toy set against numbers computed by hand.

Toy set (reference -> votes of m1, m2, m3 -> ensemble after the real aggregate):
 1 LL  [slams]          LL LL LL           -> LL 3/3 spans [slams]           correct
 2 LL  [rip-off]        LL LL NT           -> LL 2/3 spans [rip-off, huge]   correct
 3 LL  [thugs]          NT NT LL           -> NT 2/3                         wrong
 4 LL  [regime]         LL AF EM           -> disputed (NT)                  wrong
 5 AF  [will drown the city] AF AF AF      -> AF 3/3 same span               correct
 6 AF  [deadly threat]  LL LL AF           -> LL 2/3 spans [deadly]          wrong
 7 EM  [the worst ever] EM EM EM           -> EM 3/3 spans [worst ever]      correct
 8 NT                   NT NT NT           -> NT 3/3                         correct
 9 NT                   NT NT D            -> NT 2/3                         correct
10 NT                   LL LL LL           -> LL 3/3 (false positive)        wrong
11 NT                   D  D  NT           -> D 2/3 (false positive)         wrong
12 NT                   AF D  NT           -> disputed (NT)                  correct
"""

from __future__ import annotations

import pytest
from app.core.aggregate import aggregate
from app.core.types import ModelVote
from eval.calibration import by_agreement, flag_policies
from eval.metrics import EvalItem, ItemResult, source_metrics
from eval.wilson import proportion, wilson_interval

LL, AF, EM, D, NT = (
    "loaded_language",
    "appeal_to_fear",
    "exaggeration_minimization",
    "doubt",
    "no_technique",
)

# (reference, reference spans, [(technique, spans) for m1, m2, m3])
TOY = [
    (LL, ["slams"], [(LL, ["slams"]), (LL, ["slams"]), (LL, ["slams"])]),
    (LL, ["rip-off"], [(LL, ["rip-off", "huge"]), (LL, ["rip-off", "huge"]), (NT, [])]),
    (LL, ["thugs"], [(NT, []), (NT, []), (LL, ["thugs"])]),
    (LL, ["regime"], [(LL, ["regime"]), (AF, []), (EM, [])]),
    (AF, ["will drown the city"], [(AF, ["will drown the city"])] * 3),
    (AF, ["deadly threat"], [(LL, ["deadly"]), (LL, ["deadly"]), (AF, ["deadly threat"])]),
    (EM, ["the worst ever"], [(EM, ["worst ever"])] * 3),
    (NT, [], [(NT, [])] * 3),
    (NT, [], [(NT, []), (NT, []), (D, ["maybe"])]),
    (NT, [], [(LL, ["bold"])] * 3),
    (NT, [], [(D, ["questions"]), (D, ["questions"]), (NT, [])]),
    (NT, [], [(AF, []), (D, []), (NT, [])]),
]


def toy_results() -> list[ItemResult]:
    results = []
    for i, (ref, ref_spans, votes) in enumerate(TOY, 1):
        model_votes = [
            ModelVote(model=f"m{j}", technique=t, evidence_spans=list(s), explanation="x")
            for j, (t, s) in enumerate(votes, 1)
        ]
        item = EvalItem(f"toy-{i}", "toy", f"text {i}", ref, tuple(ref_spans))
        results.append(ItemResult(item, aggregate(model_votes, n_models=3, min_accuse=2)))
    return results


@pytest.fixture(scope="module")
def toy() -> dict:
    return source_metrics(toy_results(), label="toy", excluded=None)


def test_wilson_matches_reference_values() -> None:
    assert proportion(2, 5) == {"k": 2, "n": 5, "value": 0.4, "ci": [0.1176, 0.7693]}
    assert proportion(0, 0) == {"k": 0, "n": 0, "value": None, "ci": None}
    with pytest.raises(ValueError):
        wilson_interval(3, 2)


def test_toy_accuracy_and_fpr(toy: dict) -> None:
    assert toy["n"] == 12 and toy["n_errors"] == 0
    assert (toy["accuracy"]["k"], toy["accuracy"]["n"]) == (7, 12)
    assert toy["accuracy"]["value"] == pytest.approx(0.5833, abs=1e-4)
    assert toy["fpr_no_technique"] == proportion(2, 5)  # items 10 and 11 of 5 clean texts


def test_toy_per_technique(toy: dict) -> None:
    per = toy["per_technique"]
    expected = {  # support, predicted, tp, f1
        LL: (4, 4, 2, 0.5),
        AF: (2, 1, 1, 0.6667),
        EM: (1, 1, 1, 1.0),
        D: (0, 1, 0, 0.0),
        "vague_authority": (0, 0, 0, None),
        "changing_subject": (0, 0, 0, None),
        NT: (5, 5, 3, 0.6),
    }
    for label, (support, predicted, tp, f1) in expected.items():
        row = per[label]
        assert (row["support"], row["predicted"], row["tp"]) == (support, predicted, tp), label
        assert row["f1"] == (None if f1 is None else pytest.approx(f1, abs=1e-4)), label
        assert row["precision"] == proportion(tp, predicted)
        assert row["recall"] == proportion(tp, support)
    # macro-F1 over the 5 labels seen as reference or prediction: (0.5+2/3+1+0+0.6)/5
    assert toy["macro_f1"]["labels"] == 5
    assert toy["macro_f1"]["value"] == pytest.approx(0.5533, abs=1e-4)
    lo, hi = toy["macro_f1"]["ci"]
    assert 0 <= lo <= hi <= 1


def test_toy_confusion_matrix(toy: dict) -> None:
    cm = toy["confusion_matrix"]
    assert cm["rows"] == "reference" and cm["columns"] == "predicted"
    assert cm["matrix"] == [
        [2, 0, 0, 0, 0, 0, 2],  # LL
        [1, 1, 0, 0, 0, 0, 0],  # AF
        [0, 0, 1, 0, 0, 0, 0],  # EM
        [0] * 7,  # doubt
        [0] * 7,  # vague_authority
        [0] * 7,  # changing_subject
        [1, 0, 0, 1, 0, 0, 3],  # NT
    ]


def test_toy_span_token_f1(toy: dict) -> None:
    span = toy["span_token_f1"]
    # items 1-7: 1, 2/3, 0, 0, 1, 2/3, 4/5 -> 4.1333/7
    assert span["n"] == 7
    assert span["mean"] == pytest.approx(0.5905, abs=1e-4)
    # correct technique only (items 1, 2, 5, 7): (1 + 2/3 + 1 + 4/5) / 4
    assert span["when_technique_correct"]["n"] == 4
    assert span["when_technique_correct"]["mean"] == pytest.approx(0.8667, abs=1e-4)


def test_toy_agreement_and_policies() -> None:
    results = toy_results()
    levels = {row["level"]: row["accuracy"] for row in by_agreement(results)}
    assert list(levels) == ["3/3", "2/3", "disputed"]
    assert (levels["3/3"]["k"], levels["3/3"]["n"]) == (4, 5)
    assert (levels["2/3"]["k"], levels["2/3"]["n"]) == (2, 5)
    assert (levels["disputed"]["k"], levels["disputed"]["n"]) == (1, 2)

    two, three = flag_policies(results)
    assert two["fpr_no_technique"] == proportion(2, 5)
    assert two["recall_any_technique"] == proportion(5, 7)
    assert two["recall_correct_technique"] == proportion(4, 7)
    assert three["fpr_no_technique"] == proportion(1, 5)
    assert three["recall_any_technique"] == proportion(3, 7)


def test_toy_per_model(toy: dict) -> None:
    rows = {row["model"]: row for row in toy["per_model"]}
    assert list(rows) == ["m1", "m2", "m3", "ensemble"]
    m1 = rows["m1"]
    assert m1["n_valid_votes"] == 12
    assert (m1["accuracy"]["k"], m1["accuracy"]["n"]) == (7, 12)
    assert m1["fpr_no_technique"] == proportion(3, 5)
    # LL 6/9, AF 2/4, EM 1, D 0, NT 4/8 -> 2.6667/5
    assert m1["macro_f1"] == pytest.approx(0.5333, abs=1e-4)
    assert rows["ensemble"]["accuracy"] == toy["accuracy"]
    assert rows["ensemble"]["macro_f1"] == toy["macro_f1"]["value"]


def test_failed_items_are_counted_not_scored() -> None:
    results = toy_results()
    failed = ItemResult(EvalItem("toy-x", "toy", "t", NT, ()), None, "RuntimeError: boom")
    metrics = source_metrics([*results, failed], label="toy", excluded=None)
    assert (metrics["n"], metrics["n_total"], metrics["n_errors"]) == (12, 13, 1)
    assert metrics["error_ids"] == ["toy-x"]
