"""
scoring.py — Pure reputation formula. No DB, no HTTP, no side effects.

Formula:
    reputation_score = (
        success_rate      * 0.50 +
        reliability_score * 0.20 +
        time_score        * 0.15 +
        payment_score     * 0.15
    ) * 100

    registry_score = reputation_score / 20   (maps 0–100 → 0.00–5.00)

Component definitions:
    success_rate      = successful_tasks / total_tasks
    reliability_score = successful_tasks / (successful_tasks + timed_out_tasks)
                        — measures whether agent responds vs going silent
    time_score        = 1 - (avg_response_ms / max_acceptable_ms), clamped 0–1
                        — NULL response_ms (failures) contribute 0
    payment_score     = successful_payments / successful_tasks
                        — only meaningful for completed tasks
"""

from decimal import Decimal, ROUND_HALF_UP


# ── Constants ─────────────────────────────────────────────────────────────────

WEIGHT_SUCCESS     = Decimal("0.50")
WEIGHT_RELIABILITY = Decimal("0.20")
WEIGHT_TIME        = Decimal("0.15")
WEIGHT_PAYMENT     = Decimal("0.15")

SCORE_SCALE   = Decimal("100")
REGISTRY_SCALE = Decimal("20")   # reputation_score / 20 → 0.00–5.00

_FOUR_DP = Decimal("0.0001")
_TWO_DP  = Decimal("0.01")


# ── Component calculators ─────────────────────────────────────────────────────

def calc_success_rate(successful: int, total: int) -> Decimal:
    """successful / total. Returns 0 if no tasks yet."""
    if total == 0:
        return Decimal("0.0000")
    return (Decimal(successful) / Decimal(total)).quantize(_FOUR_DP, ROUND_HALF_UP)


def calc_reliability_score(successful: int, timed_out: int) -> Decimal:
    """
    successful / (successful + timed_out).
    Failures don't count against reliability — only silent timeouts do.
    An agent that responds with an error is more reliable than one that vanishes.
    """
    denominator = successful + timed_out
    if denominator == 0:
        return Decimal("0.0000")
    return (Decimal(successful) / Decimal(denominator)).quantize(_FOUR_DP, ROUND_HALF_UP)


def calc_time_score(
    total_response_ms: int,
    successful_tasks: int,
    max_acceptable_ms: int,
) -> Decimal:
    """
    1 - (avg_response_ms / max_acceptable_ms), clamped to [0, 1].
    Only successful tasks contribute response time — failures have no response.
    Returns 0 if no successful tasks or avg >= max_acceptable_ms.
    """
    if successful_tasks == 0 or max_acceptable_ms <= 0:
        return Decimal("0.0000")
    avg_ms = total_response_ms / successful_tasks
    raw = 1 - (avg_ms / max_acceptable_ms)
    clamped = max(0.0, min(1.0, raw))
    return Decimal(str(round(clamped, 4))).quantize(_FOUR_DP, ROUND_HALF_UP)


def calc_payment_score(successful_payments: int, successful_tasks: int) -> Decimal:
    """
    successful_payments / successful_tasks.
    Returns 0 if no successful tasks yet.
    """
    if successful_tasks == 0:
        return Decimal("0.0000")
    return (
        Decimal(successful_payments) / Decimal(successful_tasks)
    ).quantize(_FOUR_DP, ROUND_HALF_UP)


# ── Composite score ───────────────────────────────────────────────────────────

def compute_scores(
    total_tasks: int,
    successful_tasks: int,
    timed_out_tasks: int,
    total_response_ms: int,
    successful_payments: int,
    max_acceptable_ms: int,
) -> dict:
    """
    Compute all score components and the final composite scores.
    Returns a dict with all values ready to write to the DB.
    """
    success_rate = calc_success_rate(successful_tasks, total_tasks)
    reliability_score = calc_reliability_score(successful_tasks, timed_out_tasks)
    time_score = calc_time_score(total_response_ms, successful_tasks, max_acceptable_ms)
    payment_score = calc_payment_score(successful_payments, successful_tasks)

    reputation_score = (
        success_rate      * WEIGHT_SUCCESS +
        reliability_score * WEIGHT_RELIABILITY +
        time_score        * WEIGHT_TIME +
        payment_score     * WEIGHT_PAYMENT
    ) * SCORE_SCALE

    reputation_score = reputation_score.quantize(_TWO_DP, ROUND_HALF_UP)

    # Clamp to valid range — floating point operations can produce tiny overflows
    reputation_score = max(Decimal("0.00"), min(Decimal("100.00"), reputation_score))

    registry_score = (reputation_score / REGISTRY_SCALE).quantize(_TWO_DP, ROUND_HALF_UP)
    registry_score = max(Decimal("0.00"), min(Decimal("5.00"), registry_score))

    return {
        "success_rate": success_rate,
        "reliability_score": reliability_score,
        "time_score": time_score,
        "payment_score": payment_score,
        "reputation_score": reputation_score,
        "registry_score": registry_score,
    }