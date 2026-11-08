"""叙述性显式零证据：说明里明确写“某项支出决算为 0”时，为同身份空表格作证。

纪律（2026-11-04 复审 RF-02b；2026-11-07 R2.2 附页 A1 增补语气守卫）：

- 只认**同一指标**的水平陈述句（``X支出(决算)?(为)?0万元``），不认增减额句
  （``减少 0.08 万元``/``增加 0.13 万元``）；
- **只认事实断言语境**：否定（"不成立/并非/没有证据"）、条件（"若/如果/假设"）、
  引用/模板/示例（含金额整体处于引号内）、疑问一律不作零依据；支配范围覆盖
  整个零陈述句（含前缀引导句与"以下表述不成立"式指前否定），并覆盖宣告帧
  **作用域**（"以下为模板示例/假设情形"宣告后、显式闭合前的全部零陈述，
  R2-02：作用域歧义即拒收，不存在隔句自动出框架；R3-01/R4/R5：冒号引导
  片段（句首到冒号不限长度）与未终结行**结构**判据——片段/行内出现帧类词
  即建帧，帧词无需直接接冒号、长度不作授零依据，支配块内全部零陈述）；
- 阶段必须匹配：决算格只接受决算语境的零声明；年初预算格不接受决算声明；
- 年度、主体必须与材料一致，出现他年度或他主体一律拒绝；
- 同一指标在同类语境里只要出现一条**非零水平陈述**，就不形成零证据；
- 返回原页位置（页码 + 原文），供回执、复核与导出保持同一出处；
- 不回写原表、不物化印刷金额：空表格仍是空，只是“取值可被说明证明为 0”。
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

from .native_narrative_checks import _Text, _foreign_owner

#: 表 → 指标 → 名称片段（§4.1：零依据能力按表分域；FIN_02 收入表为
#: V33-240 的落点，IR 范围澄清明确其在本计划目标内）。
#: 片段按最具体优先排列，避免“公务用车购置”吞掉“购置及运行维护费”。
_TABLE_LABELS: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "FIN_07_three_public": {
        "overseas": (r"因公出国（境）费|因公出国\(境\)费",),
        "reception": (r"公务接待费",),
        "vehicle_purchase": (r"公务用车购置(?!及运行)",),
        "vehicle_purchase_operation": (r"公务用车购置及运行维护费",),
        "vehicle_operation": (r"公务用车运行维护费",),
        "total_actual": (r"“三公”经费|三公经费",),
    },
    "FIN_02_income": {
        "total_actual": (r"本年收入合计|收入合计",),
        "fiscal_allocation": (r"财政拨款收入",),
        "superior_subsidy_income": (r"上级补助收入",),
        "business_income": (r"事业收入",),
        "operational_income": (r"经营收入",),
        "subsidiary_income": (r"附属单位上缴收入",),
        "other_income": (r"其他收入",),
    },
}

#: 兼容旧引用：三公域标签。
_LABELS = _TABLE_LABELS["FIN_07_three_public"]

_NONZERO_DELTA_RE = re.compile(r"(?:增加|减少|增长|下降)")

# ---- A1 来源 A 准入 1：语气守卫（R2.2 定向补正；独立复核 IR02 加固）----
# 零陈述必须处于事实断言（assertive）语境。否定/条件/引用模板/疑问语气一律
# 拒收；支配范围覆盖整个零陈述句（含前缀引导句与"以下为假设情形/模板示例"
# 式宣告帧——宣告句支配紧随其后的零陈述，即使隔了句号）。
# 边界（诚实声明）：下列词表是保守**下界**，不宣称对任意自然语言一般安全；
# 设计原则是 fail-closed——无法确认断言语境时一律不授权零。
_NEGATION_RE = re.compile(
    r"不成立|并非|并不是|没有证据|无证据|不属实"
    r"|不得认定|不能认定|不得认为|不能认为|不应认定|不应认为"
    r"|不得视为|不能视为|不应视为|不得推定|不能推定")
# “若”排除“若干”；条件句把零陈述变成假设结果，不是本期事实。
_CONDITIONAL_RE = re.compile(r"如果|假设|假如|倘若|如若|若(?!干)")
_QUOTE_TEMPLATE_RE = re.compile(r"示例|样例|模板|仅供参考")
_QUESTION_RE = re.compile(r"[？?]")
# 前导句的指前否定："以下表述不成立"式——隔句号仍支配本句。
_FORWARD_NEGATION_RE = re.compile(r"(?:以下|下列|下述|如下)(?:表述|说法|内容|情况)?(?:均)?(?:不成立|并非|不属实)")
# 前导宣告帧（IR02）：宣告句（以下/下列/… + 假设/示例/模板…）宣布其后内容
# 处于非断言框架。真实语序"具体情况如下："不含框架名词，不受影响。
_FORWARD_REF_RE = re.compile(r"以下|下列|下述|如下|下面")
_FRAME_NOUN_RE = re.compile(r"假设|假定|示例|样例|模板|虚构|演示")
# R2-02（第二轮复核）：宣告帧是**作用域**不是单句修饰——旧实现只检查紧邻
# 前导片段且要求宣告句贴着零陈述（$ 锚），宣告后的第二条零陈述就漏网。
# 现在：宣告句开启非断言框架，作用域自宣告处起支配其后全部内容，直到
# 显式闭合或文本结束；作用域内的每一条零陈述都拒收（fail-closed）。
_FRAME_ANNOUNCEMENT_OPEN_RE = re.compile(
    rf"(?:{_FORWARD_REF_RE.pattern})[^。；;]{{0,24}}(?:{_FRAME_NOUN_RE.pattern})"
    rf"|(?:{_FRAME_NOUN_RE.pattern})[^。；;]{{0,24}}(?:{_FORWARD_REF_RE.pattern})")
#: 显式闭合："以上为模板示例/以上情形系假设/示例到此结束"式——提前结束
#: 作用域。闭合词表同为保守下界：识别不出闭合只会让作用域更长（更保守）。
_FRAME_CLOSURE_RE = re.compile(
    r"以上[^。；;]{0,16}?(?:假设|假定|示例|样例|模板|虚构|演示)"
    r"|(?:假设|假定|示例|样例|模板|虚构|演示)情形?[^。；;]{0,6}?(?:到此)?(?:结束|为止)"
    r"|到此(?:为止|结束)")
# R3-01/R4/R5（第三至五轮复核）：宣告**结构**判据——帧词识别到后，长度
# 一律不作为授零语义依据（R5：超阈值回退普通事实=错误授权，长度只可保护
# 性能）：
# - 冒号引导片段：句首（文本开头或。；;之后）到冒号之间的无句读片段
#   **不限长度**，帧词出现在片段任意位置即建帧——冒号本身就是片段边界；
# - 未终结行（raw）：整行不含句末终结符（。；！？）且含帧词 → 建帧
#   （布局无法确认是否标题 → 保守按非断言宣告处理，不做长度豁免）。
# 两种形态的作用域一致：至显式闭合或文末，跨句/段/页不自动退出。
# 已识别帧词但结构无法可靠定界时，宁保守拒收（保持未知），不默认当事实。
_FRAME_COLON_OPEN_RE = re.compile(
    rf"(?:^|[。；;])[^。；;]*?(?:{_FRAME_NOUN_RE.pattern})[^。；;]*?[：:]")
_FRAME_TITLE_LINE_RE = re.compile(
    rf"^[^。；！？]*?(?:{_FRAME_NOUN_RE.pattern})[^。；！？]*$")
#: 兼容旧名：宣告帧开启形态（无 $ 锚——锚定语义已由作用域机制取代）。
_FRAME_ANNOUNCEMENT_RE = _FRAME_ANNOUNCEMENT_OPEN_RE


def _frame_scope_spans(text: "_Text") -> Tuple[Tuple[int, int], ...]:
    """宣告帧作用域区间（R2-02/R3-01/R4）。

    三类宣告结构各自开启作用域：自宣告处起，到最早的显式闭合处或文本
    末尾为止——指前词宣告（以下为模板示例…）、冒号引导片段（句首到冒号
    不限长度，帧词在片段任意位置即建帧）、未终结行（整行无句末终结符且
    含帧词，raw 行上识别——布局无法确认是否标题时保守建帧）。作用域内的
    零陈述一律不是本材料的事实断言——不存在"隔了几个句子/段落就自动出
    框架"的默认豁免；识别到帧词但结构无法可靠定界时按保守拒收处理
    （R5：长度不作授零语义依据）。
    """
    compact = text.compact
    spans: list = []
    for open_re in (_FRAME_ANNOUNCEMENT_OPEN_RE, _FRAME_COLON_OPEN_RE):
        for match in open_re.finditer(compact):
            closure = _FRAME_CLOSURE_RE.search(compact, match.end())
            spans.append((match.start(), closure.start() if closure else len(compact)))
    # 未终结行（raw 行形态）：整行无句末终结符且含帧词 → 建帧（长度不限）。
    # 帧词所在行若以终结符结尾，属已终结正文，不在此列（逐句守卫处理）。
    raw = text.raw
    offset = 0
    for line in raw.splitlines(keepends=True):
        start, end = offset, offset + len(line)
        offset = end
        stripped = line.strip()
        if not stripped or not _FRAME_TITLE_LINE_RE.match(stripped):
            continue
        c_start, c_end = text.compact_range(start, end)
        if c_end <= c_start:
            continue
        closure = _FRAME_CLOSURE_RE.search(compact, c_start)
        spans.append((c_start, closure.start() if closure else len(compact)))
    return tuple(spans)
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


def _is_assertive_context(compact: str, begin: int, clause: str,
                          frame_spans: Tuple[Tuple[int, int], ...] = ()) -> bool:
    """语气守卫：零陈述必须处于 assertive 语境（A1 准入 1；IR02/R2-02 加固）。

    支配范围 = 整个零陈述句（含前缀引导句）：
    - 否定/条件/模板示例/疑问出现在同一句读单元内 → 拒收；
    - 前导文字以冒号/逗号直连本句且带否定/条件/模板语气 → 拒收；
    - 前导句是指前否定（"以下表述不成立"）→ 支配紧随的零陈述，隔句号仍拒收；
    - **宣告帧作用域**（R2-02）：零陈述落在任一宣告帧作用域区间内（宣告句
      之后、显式闭合之前——不管隔了多少句）→ 拒收。作用域歧义即拒收。
    语气命中的后果是"该句不作零依据"（fail-closed），不会产生错误证据。
    词表是保守下界：无法确认断言语境时不授权。
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
    if any(span_begin <= begin < span_end for span_begin, span_end in frame_spans):
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
    if table_code not in _TABLE_LABELS or stage != "决算":
        return None
    labels = _TABLE_LABELS[table_code].get(indicator)
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
    # R2-02/R4：宣告帧作用域按整份说明文本计算一次（宣告句到显式闭合/文末）。
    frame_spans = _frame_scope_spans(text)
    zero_ranges = []
    for begin, end in _clauses(compact):
        clause = compact[begin:end]
        if not pattern.search(clause):
            continue
        # A1 准入 1：语气守卫（否定/条件/引用模板/疑问/宣告帧作用域 → 非事实断言）。
        if not _is_assertive_context(compact, begin, clause, frame_spans):
            continue
        if table_code == "FIN_07_three_public" and _NON_FISCAL_SCOPE_RE.search(clause):
            # “非财政拨款…决算0万元”说的是另一资金口径，不能补财政拨款表。
            # （仅三公域适用：FIN_02 收入表的指标本身就是各收入来源名，
            # "经营收入"等字样是指标身份，不是外来口径——IR 范围澄清。）
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
