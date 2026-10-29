"""Deterministic statistics used by the six/seven calibration adapter.

The implementation preserves the historical MANE/TIDE conventions: tied
values receive their average rank, Spearman returns ``None`` for insufficient
or zero-variance samples, and correlation is rounded to four decimals.
"""

import math


def average_rank(xs):
    """Return one-based average ranks, including tied values."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(x, y):
    """Return rank correlation, or ``None`` for unusable samples."""
    n = len(x)
    if n < 2:
        return None
    rx, ry = average_rank(x), average_rank(y)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    sy = math.sqrt(sum((b - my) ** 2 for b in ry))
    if sx < 1e-9 or sy < 1e-9:
        return None
    return round(cov / (sx * sy), 4)


def median(xs):
    """Return the median, using zero for an empty historical sample."""
    if not xs:
        return 0.0
    ordered = sorted(xs)
    mid = len(ordered) // 2
    return (ordered[mid] if len(ordered) % 2
            else (ordered[mid - 1] + ordered[mid]) / 2.0)


__all__ = ("average_rank", "median", "spearman")
