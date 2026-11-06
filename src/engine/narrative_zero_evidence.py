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
from decimal import Decimal
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

#: 明确不属于财政拨款的资金口径：不能证明“财政拨款三公表”的空格为零。
#: 目标表自身是财政拨款口径，不能反过来给说明文本赋予身份。
_NON_FISCAL_SCOPE_RE = re.compile(
    r"非财政拨款|其他资金|事业收入|经营收入|上级补助|附属单位上缴|自有资金|非财政补助"
)
_CLAUSE_SPLIT = re.compile(r"[。；;]")


def _level_pattern(labels):
    return re.compile(
        rf"(?:{'|'.join(labels)})(?:财政拨款)?(?:支出)?(?:决算(?:数)?)?(?:为|是)?"
        r"(?P<num>\d[\d,]*(?:\.\d+)?)万元")


_TOTAL_DUAL_LEVEL = re.compile(
    r'(?:“三公”经费|三公经费)(?:财政拨款)?支出年初预算(?:数)?(?:为|是)?'
    r'\d[\d,]*(?:\.\d+)?万元[，,]支出决算(?:数)?(?:为|是)?(?P<num>\d[\d,]*(?:\.\d+)?)万元')


def explicit_no_activity_sources(doc, text):
    """同年度财政拨款三公全部无支出的原句，只证明决算，不证明预算。"""
    context = getattr(doc, 'fact_context', None)
    if context is None or not context.org_name or context.fiscal_year is None:
        return None
    matches = []
    pattern = re.compile(r'(?:20\d{2}年度|本年度|本年)(?:没有|无)[“”"\s]*三公[“”"\s]*经费财政拨款支出')
    for begin, end in _clauses(text.compact):
        clause = text.compact[begin:end]
        if (not pattern.search(clause) or _NON_FISCAL_SCOPE_RE.search(clause)
                or re.search(r'并非|不是|不代表|不能说明|暂无', clause)
                or _foreign_owner(clause, context.org_name)
                or any(int(y) != context.fiscal_year for y in re.findall(r'20\d{2}(?=年)', clause))
                or not _section_is_final(text, begin, doc)):
            continue
        matches.append((begin, end))
    if not matches:
        return None
    # 全部无支出与任何当前分项的非零水平陈述冲突时，不拿总述覆盖明细。
    for begin, end in _clauses(text.compact):
        clause = text.compact[begin:end]
        if ('预算' in clause and '决算' not in clause) or any(
                int(y) != context.fiscal_year for y in re.findall(r'20\d{2}(?=年)', clause)):
            continue
        for indicator, labels in _LABELS.items():
            values = [m['num'] for m in _level_pattern(labels).finditer(clause)]
            if indicator == 'total_actual':
                values += [m['num'] for m in _TOTAL_DUAL_LEVEL.finditer(clause)]
            if any(Decimal(v.replace(',', '')) != 0 for v in values):
                return None
    return [ref for begin, end in matches for ref in text.refs(begin, end, '原文明确本年度三公财政拨款决算无支出')]


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

    level = _level_pattern(labels)
    zero_ranges = []
    for begin, end in _clauses(compact):
        clause = compact[begin:end]
        if not pattern.search(clause):
            continue
        if _NON_FISCAL_SCOPE_RE.search(clause):
            # “非财政拨款…决算0万元”说的是另一资金口径，不能补财政拨款表。
            return None
        # 含“增加/减少”的句子未必没有水平值：
        # “决算1万元，比上年增加0.20万元”仍披露了本期值 1 万元。
        # 水平值由 level 正则单独取；只有取不到水平值时才整句跳过。
        if "预算" in clause and "决算" not in clause:
            continue  # 预算口径的零不能证明决算格
        statements = [m.group("num") for m in level.finditer(clause)]
        if indicator == 'total_actual':
            # 预算与决算在同一句时只取显式“支出决算”的金额；不能把预算5读成决算5。
            statements += [m['num'] for m in _TOTAL_DUAL_LEVEL.finditer(clause)]
        if not statements:
            # 纯增减额句、表格行、名词解释、定义句都不是水平陈述
            continue
        if "决算" not in clause and not _section_is_final(text, begin, doc):
            continue
        if any(Decimal(text_value.replace(",", "")) != 0 for text_value in statements):
            # 同一指标确有非零水平陈述 → 不能形成零证据
            return None
        if _foreign_owner(clause, org_name):
            return None
        if any(int(year) != fiscal_year for year in re.findall(r"20\d{2}(?=年)", clause)):
            return None
        zero_ranges.append((begin, end))

    no_activity = explicit_no_activity_sources(doc, text)
    if not zero_ranges:
        return no_activity
    refs = []
    for begin, end in zero_ranges:
        refs.extend(text.refs(begin, end, "说明明确该项决算为0"))
    return (refs + (no_activity or [])) or None


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
