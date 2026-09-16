"""金额计算统一口径：Decimal + 显示舍入包络。

/docs/SYSTEM_ISSUES_HANDOFF_2026-09-05.md 验收口径：
0.01 万元级尾差（如合计 1,367.76 vs 分项之和 1,367.75）是显示舍入造成的
正常现象，不应判硬错误，但应输出「可能为取整误差」提示。

包络模型：父项与 n 个子项各自按两位小数显示时，最大累计舍入误差为
(n + 1) × 0.005 万元（每个显示值最多偏 0.005）。

- |diff| ≤ 0.5×tolerance_scale... 具体分级：
  - ``ok``：diff == 0（或 < 1e-9）；
  - ``rounding_hint``：0 < |diff| ≤ envelope(n) → 输出 info 提示；
  - ``mismatch``：|diff| > envelope(n) → 确定性错误。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Optional, Sequence, Tuple

# 显示精度：万元口径保留两位小数（预决算公开表的标准显示格式）
_DISPLAY_QUANT = Decimal("0.01")
_HALF_UNIT = Decimal("0.005")


def to_decimal(value) -> Optional[Decimal]:
    """安全转 Decimal；失败返回 None（不吞错成 0）。"""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        text = str(value).strip().replace(",", "").replace("，", "")
        if not text:
            return None
        return Decimal(text)
    except (InvalidOperation, ValueError, TypeError):
        return None


def display_round(value: Decimal) -> Decimal:
    """按公开表显示精度四舍五入（两位小数）。"""
    return value.quantize(_DISPLAY_QUANT, rounding=ROUND_HALF_UP)


def half_unit_for_term(scale_digits: int, unit: str = "万元") -> Decimal:
    """计算单个数值在万元口径下的最大显示舍入半步长。

    scale_digits: 原始显示的小数位数（如 '100.0' 为 1，'100.00' 为 2，'100' 为 0）
    unit: '万元' | '元' | '亿元'
    """
    scale = max(0, int(scale_digits))
    step = Decimal("10") ** (-scale)
    half = step * Decimal("0.5")
    if unit == "元":
        return half / Decimal("10000")
    elif unit == "亿元":
        return half * Decimal("10000")
    return half


def compute_dynamic_envelope(terms: Sequence[Any]) -> Decimal:
    """按每个参与项的实际原始单位与显示精度动态计算累计舍入包络。

    terms: StrictValue 对象列表，或者 (scale_digits, unit) 元组列表。
    """
    total = Decimal("0")
    for t in terms:
        if hasattr(t, "scale_digits") and hasattr(t, "unit"):
            total += half_unit_for_term(t.scale_digits, getattr(t, "unit", "万元"))
        elif isinstance(t, (tuple, list)) and len(t) >= 2:
            total += half_unit_for_term(t[0], t[1])
        elif isinstance(t, int):
            total += half_unit_for_term(t, "万元")
        else:
            total += _HALF_UNIT
    return total


def rounding_envelope(n_children: int) -> Decimal:
    """父项与 n 个子项的固定显示舍入包络（默认两位小数）：(n+1) × 0.005 万元。"""
    return (_DECIMAL_ZERO + _HALF_UNIT) * Decimal(n_children + 1)


_DECIMAL_ZERO = Decimal("0")


def classify_amount_diff(
    parent,
    children_sum,
    n_children: int = 1,
    envelope: Optional[Decimal] = None,
) -> Tuple[str, Decimal]:
    """分级父项与子项之和的差异。

    返回 (level, diff)：
    - ``ok``：完全一致；
    - ``rounding_hint``：包络内 → 应输出 info「可能为取整误差」；
    - ``mismatch``：超包络 → 确定性差异。
    """
    parent_d = to_decimal(parent)
    children_d = to_decimal(children_sum)
    if parent_d is None or children_d is None:
        return "insufficient", _DECIMAL_ZERO
    diff = abs(parent_d - children_d)
    if diff <= Decimal("0.0000001"):
        return "ok", diff
    if envelope is None:
        envelope = rounding_envelope(n_children)
    if diff <= envelope:
        return "rounding_hint", diff
    return "mismatch", diff


def amounts_equal(left, right, tolerance: str = "0.005") -> bool:
    """两金额在显示舍入意义下是否相等（默认 0.005 万元）。"""
    a = to_decimal(left)
    b = to_decimal(right)
    if a is None or b is None:
        return False
    return abs(a - b) <= Decimal(tolerance)
