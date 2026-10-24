"""金额计算统一口径：Decimal + 显示舍入包络 + portable 政策容差。

/docs/SYSTEM_ISSUES_HANDOFF_2026-09-05.md 验收口径：
0.01 万元级尾差（如合计 1,367.76 vs 分项之和 1,367.75）是显示舍入造成的
正常现象，不应判硬错误，但应输出「可能为取整误差」提示。

包络模型：父项与 n 个子项各自按两位小数显示时，最大累计舍入误差为
(n + 1) × 0.005 万元（每个显示值最多偏 0.005）。超过包络但不超过
政策容差（``rule_policy.AMOUNT_TOLERANCE_WAN``，2026-10-22 起为 0.01 万元）
仍按 AGENTS.md v3_3_portable 容差通过。

- 具体分级：
  - ``ok``：diff == 0（或 < 1e-9）；
  - ``rounding_hint``：0 < |diff| ≤ envelope(n) → 输出 info 提示；
  - ``within_policy_tolerance``：超包络但不超过政策容差 → 按政策通过；
  - ``mismatch``：|diff| > max(envelope(n), 政策容差) → 确定性错误。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Optional, Sequence, Tuple

from .rule_policy import AMOUNT_TOLERANCE_WAN, amount_difference_exceeds_policy

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


#: 金额单位 → 万元换算系数（ZC-01 / SYS-15：等值不同单位必须归一到同一金额）
_WAN_FACTORS = {
    "元": Decimal("0.0001"),
    "万元": Decimal("1"),
    "千元": Decimal("0.1"),
    "百万元": Decimal("100"),
    "亿元": Decimal("10000"),
}


def normalize_amount_to_wan(value, unit: Optional[str] = None) -> Optional[Decimal]:
    """把任意单位的金额换算到万元口径（Decimal），失败返回 None。

    ``value`` 可以是数字、数字字符串或带单位后缀的原文（如 ``"1,234.56万元"``）；
    显式传入 ``unit`` 时优先于原文内嵌单位。
    """
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    effective_unit = unit
    if not effective_unit:
        for token in ("百万元", "亿元", "万元", "千元", "元"):
            if token in text:
                effective_unit = token
                text = text.replace(token, "")
                break
    number = to_decimal(text)
    if number is None:
        return None
    if not effective_unit or effective_unit not in _WAN_FACTORS:
        # 无单位信息时不猜口径——返回 None 而不是默认万元（识别状态交给调用方标记）
        return None
    return number * _WAN_FACTORS[effective_unit]


def display_round(value: Decimal) -> Decimal:
    """按公开表显示精度四舍五入（两位小数）。"""
    return value.quantize(_DISPLAY_QUANT, rounding=ROUND_HALF_UP)


#: 原始单位 → 换算到万元时小数位的增量（元→万元多 4 位，亿元→万元少 4 位）。
_WAN_SCALE_SHIFT = {"元": 4, "千元": 1, "万元": 0, "百万元": -2, "亿元": -4}


def wan_scale(scale_digits: Any, unit: Optional[str]) -> Optional[int]:
    """原始显示小数位数换算成万元口径下的小数位数；单位未知返回 None。"""
    shift = _WAN_SCALE_SHIFT.get(str(unit or "").strip())
    if shift is None:
        return None
    try:
        return max(0, int(scale_digits)) + shift
    except (TypeError, ValueError):
        return None


def classify_display_conversion(
    text_wan: Any,
    text_scale: Any,
    table_wan: Any,
    table_wan_scale: Optional[int],
) -> Tuple[str, Optional[Decimal]]:
    """清单 C-16：正文万元数须等于表内金额按正文精度 ROUND_HALF_UP 的结果。

    只在表侧比正文更精细时适用（典型：预算表单位为元、正文写万元两位小数）。
    返回 ``(state, expected)``：``ok`` / ``mismatch`` 时 expected 为按正文精度
    四舍五入后的表内金额；表侧不比正文精细或取数不全时为 ``not_applicable``。
    截尾（102.2257→102.22）与进位错误都判 mismatch，不受政策容差豁免。
    """
    text_d = to_decimal(text_wan)
    table_d = to_decimal(table_wan)
    try:
        scale = int(text_scale)
    except (TypeError, ValueError):
        return "not_applicable", None
    if text_d is None or table_d is None or table_wan_scale is None or scale < 0:
        return "not_applicable", None
    if table_wan_scale <= scale:
        return "not_applicable", None
    expected = table_d.quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP)
    return ("ok" if text_d == expected else "mismatch"), expected


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
    - ``within_policy_tolerance``：超显示舍入包络但不超过政策容差；
    - ``mismatch``：超出 max(政策容差, 显示舍入包络)。
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
    if not amount_difference_exceeds_policy(diff, envelope):
        return "within_policy_tolerance", diff
    return "mismatch", diff


def amounts_equal(left, right, tolerance: Any = AMOUNT_TOLERANCE_WAN) -> bool:
    """两万元口径金额是否在 portable 绝对政策容差内（默认取统一政策容差）。"""
    a = to_decimal(left)
    b = to_decimal(right)
    if a is None or b is None:
        return False
    return abs(a - b) <= Decimal(str(tolerance))
