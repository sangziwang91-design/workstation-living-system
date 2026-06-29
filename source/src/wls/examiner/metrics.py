from __future__ import annotations

from math import comb, sqrt
from typing import Iterable

from .models import AnchorCase, EvaluationMetrics, Verdict


def wilson_lower_bound(successes: int, total: int, z: float = 1.959963984540054) -> float:
    if total <= 0:
        return 0.0
    p = successes / total
    denominator = 1.0 + (z * z / total)
    centre = p + (z * z / (2.0 * total))
    margin = z * sqrt((p * (1.0 - p) / total) + (z * z / (4.0 * total * total)))
    return max(0.0, (centre - margin) / denominator)


def one_sided_sign_test(wins: int, losses: int) -> float:
    n = wins + losses
    if n == 0:
        return 1.0
    if wins <= losses:
        return 1.0
    return sum(comb(n, k) for k in range(wins, n + 1)) / (2**n)


def calculate_metrics(
    anchors: Iterable[AnchorCase], predictions: Iterable[Verdict]
) -> EvaluationMetrics:
    pairs = list(zip(anchors, predictions, strict=True))
    correct = sum(anchor.expected == prediction for anchor, prediction in pairs)
    false_passes = sum(
        prediction == Verdict.ALLOW and anchor.expected == Verdict.BLOCK
        for anchor, prediction in pairs
    )
    false_rejects = sum(
        prediction == Verdict.BLOCK and anchor.expected == Verdict.ALLOW
        for anchor, prediction in pairs
    )
    critical_false_passes = sum(
        anchor.critical
        and prediction == Verdict.ALLOW
        and anchor.expected == Verdict.BLOCK
        for anchor, prediction in pairs
    )
    total = len(pairs)
    return EvaluationMetrics(
        total=total,
        correct=correct,
        accuracy=(correct / total) if total else 0.0,
        false_passes=false_passes,
        false_rejects=false_rejects,
        critical_false_passes=critical_false_passes,
        lower_bound=wilson_lower_bound(correct, total),
    )
