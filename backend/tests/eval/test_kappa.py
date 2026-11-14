from __future__ import annotations

import pytest
from eval.kappa import cohen_kappa


def test_kappa_hand_computed() -> None:
    # po = 3/4; pe = 0.5*0.25 + 0.5*0.75 = 0.5; kappa = (0.75 - 0.5) / 0.5 = 0.5
    a = {"1": "doubt", "2": "doubt", "3": "no_technique", "4": "no_technique"}
    b = {"1": "doubt", "2": "no_technique", "3": "no_technique", "4": "no_technique"}
    assert cohen_kappa(a, b) == pytest.approx(0.5)


def test_kappa_refuses_mismatched_ids() -> None:
    with pytest.raises(ValueError, match="ID sets differ"):
        cohen_kappa({"1": "doubt", "2": "doubt"}, {"1": "doubt", "3": "doubt"})
