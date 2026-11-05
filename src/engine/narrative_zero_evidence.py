"""叙述性显式零证据：说明里明确写“某项支出决算为 0”时，为同身份空表格作证。

纪律（2026-11-04 复审 RF-02b）：

- 只认**同一指标**的水平陈述句（``X支出(决算)?(为)?0万元``），不认增减额句
  （``减少 0.08 万元``/``增加 0.13 万元``）；
- 阶段必须匹配：决算格只接受决算语境的零声明；年初预算格不接受决算声明；
- 年度、主体必须与材料一致，出现他年度或他主体一律拒绝；
- 同一指标在同类语境里只要出现一条**非零水平陈述**，就不形成零证据；
- 返回原页位置（页码 + 原文），供回执、复核与导出保持同一出处；
- 不回写原表、不物化印刷金额：空表格仍是空，只是“取值可被说明证明为 0”。
"""

from __future__ import annotations

import re
from typing import Any, Optional

from .native_narrative_checks import _Text, _foreign_owner

#: 指标 → 名称片段（按最具体优先排列，避免“公务用车购置”吞掉“购置及运行维护费”）。
_LABELS = {
    "overseas": (r"因公出国（境）费|因公出国\(境\)费",),
    "reception": (r"公务接待费",),
    "vehicle_purchase": (r"公务用车购置(?!及运行)",),
    "vehicle_purchase_operation": (r"公务用车购置及运行维护费",),
    "vehicle_operation": (r"公务用车运行维护费",),
    "total_actual": (r"“三公”经费|三公经费",),
}

_NONZERO_DELTA_RE = re.compile(r"(?:增加|减少|增长|下降)")
_ZERO_RE = re.compile(r"(?:为|是)?\s*0(?:\.0+)?\s*万?元")
_CLAUSE_SPLIT = re.compile(r"[。；;]")


def _clauses(compact: str):
    """把合并文本切成句读单元，保留字符区间。"""
    start = 0
    for match in _CLAUSE_SPLIT.finditer(compact):
        yield start, match.start()
        start = match.end()
    if start < len(compact):
        yield start, len(compact)


def narrative_zero_sources(doc: Any, *, table_code: str, indicator: str,
                           stage: str) -> Optional[list]:
    """返回该指标在指定阶段的显式零出处列表；无法唯一确认时返回 None。

    只支持决算语境（真实反例第 5 份 p22）；预算阶段没有同阶段零声明时一律
    返回 None——决算零说明不能补年初预算列。
    """
    if table_code != "FIN_07_three_public" or stage != "决算":
        return None
    labels = _LABELS.get(indicator)
    if not labels:
        return None
    pattern = re.compile("|".join(labels))
    context = getattr(doc, "fact_context", None)
    org_name = getattr(context, "org_name", None)
    fiscal_year = getattr(context, "fiscal_year", None)
    text = _Text.from_pages(getattr(doc, "page_texts", []) or [])
    compact = text.compact
    if not compact:
        return None

    zero_ranges = []
    for begin, end in _clauses(compact):
        clause = compact[begin:end]
        if not pattern.search(clause):
            continue
        if _NONZERO_DELTA_RE.search(clause):
            continue  # 增减额句不是水平陈述
        if "预算" in clause and "决算" not in clause:
            continue  # 预算口径的零不能证明决算格
        if "决算" not in clause and not _section_is_final(text, begin, doc):
            continue
        if not _ZERO_RE.search(clause):
            # 同类语境的水平陈述却不是 0 → 不能形成零证据
            return None
        if _foreign_owner(clause, org_name):
            return None
        if any(int(year) != fiscal_year for year in re.findall(r"20\d{2}(?=年)", clause)):
            return None
        zero_ranges.append((begin, end))

    if not zero_ranges:
        return None
    refs = []
    for begin, end in zero_ranges:
        refs.extend(text.refs(begin, end, "说明明确该项决算为0"))
    return refs or None


def _section_is_final(text: _Text, position: int, doc: Any = None) -> bool:
    """句内没写“决算”时，用最近的说明标题判断是否处于决算语境。

    第 5 份 p22 的“公务用车购置支出为 0 万元”句内没有“决算”二字，但它位于
    “（二）‘三公’经费财政拨款支出决算具体情况说明”之下，仍属决算语境。
    """
    prefix = text.compact[:position]
    index = max(prefix.rfind("情况说明"), prefix.rfind("说明"))
    if index >= 0:
        window = prefix[max(0, index - 60):index + 12]
        return "决算" in window
    return getattr(doc, "report_kind", None) == "final"
