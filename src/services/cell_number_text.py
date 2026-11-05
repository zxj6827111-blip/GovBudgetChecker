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


def invalid_number_text(text: Any) -> bool:
    """是否“单个数字词但小数点重复”。

    只有这种形态才可能是原件印错；多个数拼在一起属读取异常。
    """
    value = str(text or "").strip()
    if not value or not any(ch.isdigit() for ch in value):
        return False
    if not single_amount_lexeme(value):
        return False
    return len(_DECIMAL_POINT_RE.findall(value)) > 1
