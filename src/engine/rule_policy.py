"""Portable fiscal-rule comparison policy (AGENTS.md v3_3_portable).

Runtime checks import these constants instead of copying tolerances into each
checker. Amounts are in 万元 and percentages are compared in percentage points.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional, Tuple

PORTABLE_RULE_POLICY_VERSION = "v3_3_portable"
AMOUNT_TOLERANCE_WAN = Decimal("0.5")
PERCENTAGE_POINT_TOLERANCE = Decimal("0.1")


def amount_mismatch_threshold(
    rounding_envelope: Optional[Decimal] = None,
    policy_tolerance_wan: Decimal = AMOUNT_TOLERANCE_WAN,
) -> Decimal:
    """Return the larger of the per-operand display envelope and policy tolerance."""
    envelope = rounding_envelope or Decimal("0")
    return max(abs(envelope), abs(policy_tolerance_wan))


def amount_difference_exceeds_policy(
    difference: Any,
    rounding_envelope: Optional[Decimal] = None,
    policy_tolerance_wan: Decimal = AMOUNT_TOLERANCE_WAN,
) -> bool:
    """Whether a 万元 difference is outside both permitted tolerance windows."""
    try:
        diff = abs(Decimal(str(difference)))
    except Exception:
        return False
    return diff > amount_mismatch_threshold(rounding_envelope, policy_tolerance_wan)


def classify_percentage_point_difference(
    left: Any,
    right: Any,
    tolerance: Decimal = PERCENTAGE_POINT_TOLERANCE,
) -> Tuple[str, Decimal]:
    """Compare percentages as percentage points; never use a relative ratio."""
    try:
        difference = abs(Decimal(str(left)) - Decimal(str(right)))
    except Exception:
        return "insufficient", Decimal("0")
    if difference == 0:
        return "ok", difference
    if difference <= abs(tolerance):
        return "within_policy_tolerance", difference
    return "mismatch", difference


def percentage_difference_exceeds_policy(
    difference: Any,
    tolerance: Decimal = PERCENTAGE_POINT_TOLERANCE,
) -> bool:
    """Whether an absolute percentage-point difference exceeds portable policy."""
    try:
        return abs(Decimal(str(difference))) > abs(tolerance)
    except Exception:
        return False
