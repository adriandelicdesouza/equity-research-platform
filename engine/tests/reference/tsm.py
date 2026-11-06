"""Treasury-stock method by brute-force subset search (methodology §10.2).

Every subset I of option tranches is tried; P_I = (E + Σ_I nK) / (S + Σ_I n). The
consistent subsets are those with K < P_I for i ∈ I and K ≥ P_I for i ∉ I. The
methodology's fixed-point iteration must land on one of them; here we require exactly one.
"""

from __future__ import annotations

from fractions import Fraction
from itertools import combinations


def solve(
    equity: Fraction, basic_shares: Fraction, tranches: list[tuple[Fraction, Fraction]]
) -> tuple[Fraction, Fraction, tuple[int, ...]]:
    consistent = []
    idx = range(len(tranches))
    for size in range(len(tranches) + 1):
        for subset in combinations(idx, size):
            n = sum((tranches[i][0] for i in subset), Fraction(0))
            nk = sum((tranches[i][0] * tranches[i][1] for i in subset), Fraction(0))
            price = (equity + nk) / (basic_shares + n)
            inside = all(tranches[i][1] < price for i in subset)
            outside = all(tranches[i][1] >= price for i in idx if i not in subset)
            if inside and outside:
                consistent.append((price, subset))
    if len(consistent) != 1:
        raise ValueError(f"expected exactly one consistent TSM solution, found {consistent}")
    price, subset = consistent[0]
    diluted = basic_shares + sum(
        (tranches[i][0] * (1 - tranches[i][1] / price) for i in subset), Fraction(0)
    )
    return price, diluted, subset
