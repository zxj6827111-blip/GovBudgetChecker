"""严格数值/字段取数基础设施（Contract C2 实现）。

提供严格四态取数模型（有效值、明确零、空白、未识别/越界/缺列），保留原始文本、
Decimal、原始单位、原始显示精度、归一化金额、来源位置和列定位依据。
禁止将缺失、空白、越界或无效值隐式转为 0.0。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, List, Optional, Sequence

STATUS_VALID = "valid"
STATUS_ZERO = "zero"
STATUS_BLANK = "blank"
STATUS_INVALID = "invalid"
STATUS_OUT_OF_BOUNDS = "out_of_bounds"
STATUS_MISSING_COLUMN = "missing_column"

# 正则表达式：匹配单元格中的数字与可选单位
_NUMERIC_CELL_RE = re.compile(
    r"^\s*([+-]?(?:\d{1,3}(?:,\d{3})*|\d+))(\.\d+)?\s*(亿元|万元|元)?\s*$"
)
# 单独的横杠或表示无的符号
_DASH_RE = re.compile(r"^\s*[-—–一/]{1,3}\s*$")


@dataclass
class StrictValue:
    """严格取数结果对象。

    保留字段：
    - raw_text: 原始文本表示
    - status: STATUS_VALID | STATUS_ZERO | STATUS_BLANK | STATUS_INVALID | STATUS_OUT_OF_BOUNDS | STATUS_MISSING_COLUMN
    - decimal_val: 归一化为「万元」口径的 Decimal 数值（仅在 is_numeric 时非 None）
    - raw_decimal: 原始单位下的 Decimal 数值（未归一化）
    - unit: 原始单位（"万元" | "元" | "亿元"）
    - scale_digits: 原始显示的有效小数位数（如 "100.0" 为 1，"100.00" 为 2，"100" 为 0）
    - col_index: 来源列索引
    - col_name: 绑定的列名称
    - reason: 缺失、越界或无效的原因描述
    """

    raw_text: str
    status: str
    decimal_val: Optional[Decimal] = None
    raw_decimal: Optional[Decimal] = None
    unit: str = "万元"
    scale_digits: int = 2
    col_index: Optional[int] = None
    col_name: Optional[str] = None
    reason: Optional[str] = None

    @property
    def is_numeric(self) -> bool:
        """是否包含有效数值（含明确零）。"""
        return self.status in (STATUS_VALID, STATUS_ZERO) and self.decimal_val is not None

    @property
    def is_zero(self) -> bool:
        """是否为明确的零值。"""
        return self.status == STATUS_ZERO

    @property
    def is_missing(self) -> bool:
        """是否因缺失、空白、越界或未识别而无法参与数值计算。"""
        return self.status in (
            STATUS_BLANK,
            STATUS_INVALID,
            STATUS_OUT_OF_BOUNDS,
            STATUS_MISSING_COLUMN,
        )

    def as_float(self) -> Optional[float]:
        """安全转换为 float（供向后兼容接口使用）。"""
        if self.is_numeric and self.decimal_val is not None:
            return float(self.decimal_val)
        return None

    def __repr__(self) -> str:
        if self.is_numeric:
            return (
                f"StrictValue(status='{self.status}', decimal_val={self.decimal_val} {self.unit}, "
                f"scale={self.scale_digits}, col='{self.col_name or self.col_index}')"
            )
        return (
            f"StrictValue(status='{self.status}', raw='{self.raw_text}', "
            f"col='{self.col_name or self.col_index}', reason='{self.reason}')"
        )


def parse_strict_cell(
    cell: Any,
    default_unit: str = "万元",
    col_index: Optional[int] = None,
    col_name: Optional[str] = None,
) -> StrictValue:
    """严格解析单元格内容为 StrictValue。

    - 空字符串、全空格、None -> STATUS_BLANK；
    - 明确数字（如 "0", "0.00", "123.45"）-> STATUS_ZERO 或 STATUS_VALID，并保留原始小数精度；
    - 无明确约定占位符（如 "-", "/", "一"）-> STATUS_BLANK，保持未知，禁止作为零；
    - 非法或无法识别文本、非有限数（NaN/Infinity）-> STATUS_INVALID，记录 reason；
    - 统一归一化为「万元」：元 / 10000，亿元 * 10000。
    """
    if cell is None:
        return StrictValue(
            raw_text="",
            status=STATUS_BLANK,
            unit=default_unit,
            col_index=col_index,
            col_name=col_name,
            reason="单元格为 None",
        )

    raw_text = str(cell).strip()
    if not raw_text:
        return StrictValue(
            raw_text="",
            status=STATUS_BLANK,
            unit=default_unit,
            col_index=col_index,
            col_name=col_name,
            reason="单元格为空文本",
        )

    # 检查横杠/斜杠占位符：无明确约定保持空白未知，不视为数值0
    if _DASH_RE.match(raw_text):
        return StrictValue(
            raw_text=raw_text,
            status=STATUS_BLANK,
            unit=default_unit,
            col_index=col_index,
            col_name=col_name,
            reason="占位符无明确数值约定，保持空白未知",
        )

    # 规范化：去除千分位逗号与全角逗号
    clean_text = raw_text.replace(",", "").replace("，", "").strip()

    # 正则提取数字和单位
    m = _NUMERIC_CELL_RE.match(clean_text)
    if not m:
        # 尝试直接使用 Decimal 容错转换
        try:
            val = Decimal(clean_text)
            if not val.is_finite():
                return StrictValue(
                    raw_text=raw_text,
                    status=STATUS_INVALID,
                    unit=default_unit,
                    col_index=col_index,
                    col_name=col_name,
                    reason=f"非有限数值: '{raw_text}'",
                )
            scale = 0
            if "." in clean_text:
                scale = len(clean_text.split(".")[1])
            is_z = val == Decimal("0")
            if default_unit == "元":
                norm_val = val / Decimal("10000")
            elif default_unit == "亿元":
                norm_val = val * Decimal("10000")
            else:
                norm_val = val
            return StrictValue(
                raw_text=raw_text,
                status=STATUS_ZERO if is_z else STATUS_VALID,
                decimal_val=norm_val,
                raw_decimal=val,
                unit=default_unit,
                scale_digits=scale,
                col_index=col_index,
                col_name=col_name,
            )
        except (InvalidOperation, ValueError):
            return StrictValue(
                raw_text=raw_text,
                status=STATUS_INVALID,
                unit=default_unit,
                col_index=col_index,
                col_name=col_name,
                reason=f"非数字文本无法解析: '{raw_text}'",
            )

    int_part = m.group(1)
    dec_part = m.group(2) or ""
    unit_part = m.group(3) or default_unit

    number_str = int_part + dec_part
    scale = len(dec_part) - 1 if dec_part else 0
    if scale < 0:
        scale = 0

    try:
        raw_val = Decimal(number_str)
        if not raw_val.is_finite():
            return StrictValue(
                raw_text=raw_text,
                status=STATUS_INVALID,
                unit=unit_part,
                col_index=col_index,
                col_name=col_name,
                reason=f"非有限数值: '{number_str}'",
            )
    except InvalidOperation:
        return StrictValue(
            raw_text=raw_text,
            status=STATUS_INVALID,
            unit=unit_part,
            col_index=col_index,
            col_name=col_name,
            reason=f"Decimal转换失败: '{number_str}'",
        )

    # 单位归一化为万元
    if unit_part == "元":
        norm_val = raw_val / Decimal("10000")
    elif unit_part == "亿元":
        norm_val = raw_val * Decimal("10000")
    else:
        norm_val = raw_val

    is_zero = raw_val == Decimal("0")
    return StrictValue(
        raw_text=raw_text,
        status=STATUS_ZERO if is_zero else STATUS_VALID,
        decimal_val=norm_val,
        raw_decimal=raw_val,
        unit=unit_part,
        scale_digits=scale,
        col_index=col_index,
        col_name=col_name,
    )


def find_column_index(
    headers: Sequence[str],
    target_keys: Sequence[str],
    exclude_keys: Sequence[str] = (),
) -> Optional[int]:
    """在表头列表中精准定位列索引。

    - 优先精准匹配（完全包含关键词且不含排除词）；
    - 若有多个候选列，优先选择完全相等的唯一列名；
    - 若有多个完全相等列名（如同名列重复），返回 None 报告歧义，不猜列；
    - 若无完全相等但有多个部分匹配候选，仅在有唯一最短候选时采纳，否则返回 None。
    """
    cleaned_headers = [str(h or "").strip().replace(" ", "") for h in headers]

    matches: List[int] = []
    for idx, h in enumerate(cleaned_headers):
        if not h:
            continue
        if any(ex in h for ex in exclude_keys):
            continue
        if any(tk in h for tk in target_keys):
            matches.append(idx)

    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]

    # 多个匹配时消歧：寻找是否有完全相等的列名
    exact_matches: List[int] = []
    for m_idx in matches:
        for tk in target_keys:
            if cleaned_headers[m_idx] == tk:
                exact_matches.append(m_idx)
                break

    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        # 重复同名列无法唯一定位，返回 None 避免猜列
        return None

    # 无完全相等列名时：检查是否有唯一的最短候选列
    min_len = min(len(cleaned_headers[i]) for i in matches)
    shortest = [i for i in matches if len(cleaned_headers[i]) == min_len]
    if len(shortest) == 1:
        return shortest[0]

    # 多个候选无法唯一定位，返回 None
    return None


def extract_row_strict(
    row: Sequence[Any],
    col_idx: Optional[int],
    col_name: str,
    default_unit: str = "万元",
) -> StrictValue:
    """针对行数据严格提取指定列的值。

    - 若 col_idx 为 None -> STATUS_MISSING_COLUMN；
    - 若 col_idx >= len(row) -> STATUS_OUT_OF_BOUNDS；
    - 否则调用 parse_strict_cell。
    """
    if col_idx is None:
        return StrictValue(
            raw_text="",
            status=STATUS_MISSING_COLUMN,
            unit=default_unit,
            col_name=col_name,
            reason=f"未定位到列 '{col_name}'",
        )
    if col_idx >= len(row):
        return StrictValue(
            raw_text="",
            status=STATUS_OUT_OF_BOUNDS,
            unit=default_unit,
            col_index=col_idx,
            col_name=col_name,
            reason=f"列索引越界: 目标列 {col_idx} >= 行长度 {len(row)}",
        )
    cell = row[col_idx]
    return parse_strict_cell(
        cell,
        default_unit=default_unit,
        col_index=col_idx,
        col_name=col_name,
    )
