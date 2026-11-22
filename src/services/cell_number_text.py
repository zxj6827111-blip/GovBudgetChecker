"""原件单元格金额原文的分类：单个非法数词 vs 多个数被拼在一起。

同一判断同时服务两处，避免各写一套：

- ``structured_ingest_runner`` 生成待复核项（展示层）；
- ``rule_identity`` 生成规则准入回执（检查链底层）。

真实反例（2026-11-04 复审 RF-01）：
- ``2.359.46`` 是一个数字词里出现两个小数点 → 原件数字格式缺陷；
- ``1.00\\n2.00`` 是两个金额被换行拼到一起 → 提取/合并异常，不能断定纸面错数。
"""

from __future__ import annotations

import re
from typing import Any

_DECIMAL_POINT_RE = re.compile(r"[.．]")

#: 单元格末尾可能带单位；判断“是否单个数字词”时先剥掉，
#: 否则 ``2.359.46 万元`` 会被误当成两个词。
_TRAILING_UNIT_RE = re.compile(r"^(.*?)\s*(?:万\s*元|亿\s*元|千\s*元|元)$")


def single_amount_lexeme(text: Any) -> bool:
    """整个格内容是否只有一个数字词（允许带单位、不含换行/内部空白）。"""
    value = str(text or "").strip()
    if not value or "\n" in value or "\r" in value:
        return False
    match = _TRAILING_UNIT_RE.match(value)
    if match:
        value = match.group(1).strip()
    return bool(value) and not re.search(r"\s", value)


_THOUSANDS_BODY_RE = re.compile(r"^(?P<int>[0-9,]+)(?:\.(?P<frac>\d+))?$")


def _invalid_thousands_grouping(value: str) -> bool:
    """千分位分组是否非法（如 ``79,85.49`` / ``1,8415.60``）。

    分组非法时同一串字符至少有两种可解释读数（去掉逗号 / 把逗号当小数点），
    不能唯一还原；只报格式缺陷，不采用任何候选值。
    """
    match = _THOUSANDS_BODY_RE.match(value)
    if match is None or "," not in match.group("int"):
        return False
    groups = match.group("int").split(",")
    if not groups or not all(group.isdigit() for group in groups):
        return True
    if len(groups[0]) not in (1, 2, 3):
        return True
    return any(len(group) != 3 for group in groups[1:])


def invalid_number_text(text: Any) -> bool:
    """是否“单个数字词但数字格式不可唯一还原”。

    覆盖两类纸面/抽取格式缺陷：
    - 小数点重复（``2.359.46``）；
    - 千分位分组非法（``79,85.49``、``1,8415.60``）。

    多个数拼在一起（换行/空格分隔）属读取异常，不在此列。
    """
    value = str(text or "").strip()
    if not value or not any(ch.isdigit() for ch in value):
        return False
    if not single_amount_lexeme(value):
        return False
    match = _TRAILING_UNIT_RE.match(value)
    body = match.group(1).strip() if match else value
    if len(_DECIMAL_POINT_RE.findall(body)) > 1:
        return True
    return _invalid_thousands_grouping(body)
