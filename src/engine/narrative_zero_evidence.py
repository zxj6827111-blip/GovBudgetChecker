"""叙述性显式零证据：说明里明确写“某项支出决算为 0”时，为同身份空表格作证。

纪律（2026-11-04 复审 RF-02b；2026-11-07 R2.2 附页 A1 增补语气守卫）：

- 只认**同一指标**的水平陈述句（``X支出(决算)?(为)?0万元``），不认增减额句
  （``减少 0.08 万元``/``增加 0.13 万元``）；
- **只认事实断言语境**：否定（"不成立/并非/没有证据"）、条件（"若/如果/假设"）、
  引用/模板/示例（含金额整体处于引号内）、疑问一律不作零依据；支配范围覆盖
  整个零陈述句（含前缀引导句与"以下表述不成立"式指前否定）；
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

# ---- A1 来源 A 准入 1：语气守卫（R2.2 定向补正）----
# 零陈述必须处于事实断言（assertive）语境。否定/条件/引用模板/疑问语气一律
# 拒收；支配范围覆盖整个零陈述句（含前缀引导句）。zero_basis_probes.json 的
# 三个语气负控（否定句/条件句/模板示例句）在旧实现里均误收为合法零依据。
_NEGATION_RE = re.compile(r"不成立|并非|并不是|没有证据|无证据|不属实")
# “若”排除“若干”；条件句把零陈述变成假设结果，不是本期事实。
_CONDITIONAL_RE = re.compile(r"如果|假设|假如|倘若|如若|若(?!干)")
_QUOTE_TEMPLATE_RE = re.compile(r"示例|样例|模板|仅供参考")
_QUESTION_RE = re.compile(r"[？?]")
# 前导句的"以下表述不成立"式否定：即使与零陈述隔了句号，"以下"仍明确指向前方。
_FORWARD_NEGATION_RE = re.compile(r"(?:以下|下列|下述|如下)(?:表述|说法|内容|情况)?(?:均)?(?:不成立|并非|不属实)")
_QUOTE_PAIRS = (("“", "”"), ("\"", "\""))

#: 明确不属于财政拨款的资金口径：不能证明“财政拨款三公表”的空格为零。
#: 目标表自身是财政拨款口径，不能反过来给说明文本赋予身份。
_NON_FISCAL_SCOPE_RE = re.compile(
    r"非财政拨款|其他资金|事业收入|经营收入|上级补助|附属单位上缴|自有资金|非财政补助"
)
_CLAUSE_SPLIT = re.compile(r"[。；;]")

#: narrative_zero_sources 的显式身份覆盖哨兵（与 None 区分：None 是合法的"未知身份"）。
_UNSET = object()


def _clauses(compact: str):
    """把合并文本切成句读单元，保留字符区间。"""
    start = 0
    for match in _CLAUSE_SPLIT.finditer(compact):
        yield start, match.start()
        start = match.end()
    if start < len(compact):
        yield start, len(compact)


def _inside_quote_span(clause: str, start: int, end: int) -> bool:
    """金额断言整体落在引号内（“…”/"…"）时是引用，不是本材料的断言。

    注意“三公”经费这类指标自带引号：只看**金额数字**是否处于某个引号对
    内部，指标名上的短引号不影响合法陈述。
    """
    for open_ch, close_ch in _QUOTE_PAIRS:
        span_start = None
        for idx, ch in enumerate(clause):
            if ch == open_ch and span_start is None:
                span_start = idx
            elif ch == close_ch and span_start is not None:
                if span_start <= start and end <= idx:
                    return True
                span_start = None
    return False


def _is_assertive_context(compact: str, begin: int, clause: str) -> bool:
    """语气守卫：零陈述必须处于 assertive 语境（A1 准入 1）。

    支配范围 = 整个零陈述句（含前缀引导句）：
    - 否定/条件/模板示例/疑问出现在同一句读单元内 → 拒收；
    - 前导文字以冒号/逗号直连本句且带否定/条件/模板语气 → 拒收；
    - 前导句是"以下表述不成立"式指前否定 → 拒收（隔句号仍支配本句）。
    语气命中的后果是"该句不作零依据"（fail-closed），不会产生错误证据。
    """
    for regex in (_NEGATION_RE, _CONDITIONAL_RE, _QUOTE_TEMPLATE_RE):
        if regex.search(clause):
            return False
    if _QUESTION_RE.search(clause):
        return False
    lead = compact[max(0, begin - 40):begin]
    if re.search(r"[：:，,]\s*$", lead):
        for regex in (_NEGATION_RE, _CONDITIONAL_RE, _QUOTE_TEMPLATE_RE):
            if regex.search(lead):
                return False
    if _FORWARD_NEGATION_RE.search(lead):
        return False
    return True


def narrative_zero_sources(doc: Any, *, table_code: str, indicator: str,
                           stage: str, org_name: Any = _UNSET,
                           fiscal_year: Any = _UNSET) -> Optional[list]:
    """返回该指标在指定阶段的显式零出处列表；无法唯一确认时返回 None。

    只支持决算语境（真实反例第 5 份 p22）；预算阶段没有同阶段零声明时一律
    返回 None——决算零说明不能补年初预算列。

    org_name/fiscal_year 缺省读 doc.fact_context；调用方（如跨表规则）已经
    用表格页头核实过财政年度时，可显式传入同一身份（R2.2 附页 A1）——身份
    纪律不变：传入值与事实上下文不一致时以传入值参与守卫，两处都不认他
    年度/他主体。
    """
    if table_code != "FIN_07_three_public" or stage != "决算":
        return None
    labels = _LABELS.get(indicator)
    if not labels:
        return None
    pattern = re.compile("|".join(labels))
    context = getattr(doc, "fact_context", None)
    if org_name is _UNSET:
        org_name = getattr(context, "org_name", None)
    if fiscal_year is _UNSET:
        fiscal_year = getattr(context, "fiscal_year", None)
    text = _Text.from_pages(getattr(doc, "page_texts", []) or [])
    compact = text.compact
    if not compact:
        return None

    level = re.compile(
        rf"(?:{ '|'.join(labels) })\s*(?:支出)?\s*(?:决算(?:数)?)?\s*(?:为|是)?\s*"
        r"(?P<num>\d[\d,]*(?:\.\d+)?)\s*万元"
    )
    zero_ranges = []
    for begin, end in _clauses(compact):
        clause = compact[begin:end]
        if not pattern.search(clause):
            continue
        # A1 准入 1：语气守卫（否定/条件/引用模板/疑问 → 非事实断言）。
        if not _is_assertive_context(compact, begin, clause):
            continue
        if _NON_FISCAL_SCOPE_RE.search(clause):
            # “非财政拨款…决算0万元”说的是另一资金口径，不能补财政拨款表。
            return None
        # 含“增加/减少”的句子未必没有水平值：
        # “决算1万元，比上年增加0.20万元”仍披露了本期值 1 万元。
        # 水平值由 level 正则单独取；只有取不到水平值时才整句跳过。
        if "预算" in clause and "决算" not in clause:
            continue  # 预算口径的零不能证明决算格
        matches = list(level.finditer(clause))
        statements = [m.group("num") for m in matches]
        if not statements:
            # 纯增减额句、表格行、名词解释、定义句都不是水平陈述
            continue
        if any(_inside_quote_span(clause, m.start("num"), m.end("num")) for m in matches):
            # 金额整体处于引号内 = 引用/示例，不是本材料对本项的断言
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
