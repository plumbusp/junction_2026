"""Wilson score interval for a binomial proportion, and the proportion object of the results JSON."""

from __future__ import annotations

import math

Z95 = 1.959963984540054  # two-sided 95% normal quantile
DIGITS = 4  # rounding of values written to the results JSON


def wilson_interval(k: int, n: int, z: float = Z95) -> tuple[float, float] | None:
    """Wilson score interval for ``k`` successes out of ``n``; None when ``n == 0``."""
    if n < 0 or k < 0 or k > n:
        raise ValueError(f"need 0 <= k <= n, got k={k}, n={n}")
    if n == 0:
        return None
    p = k / n
    z2 = z * z
    denom = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def proportion(k: int, n: int) -> dict:
    """``{k, n, value, ci}`` with a Wilson 95% interval; value and ci are None when n == 0."""
    ci = wilson_interval(k, n)
    return {
        "k": k,
        "n": n,
        "value": round(k / n, DIGITS) if n else None,
        "ci": [round(ci[0], DIGITS), round(ci[1], DIGITS)] if ci else None,
    }
