"""检查点清单：预决算通用的文字类规则（全系统默认检查指标，2026-10-22）。

| 规则 | 清单条目 | 内容 |
|------|----------|------|
| CMM-002（扩展） | D-02/D-03/D-04 | 词组重复、单字重复、标点重复与配对、文号格式 |
| CMM-008 | D-05/D-06、A-03/A-12 提示语 | 模板二选一残留、编辑批注、模板提示语、X0 误植 |
| CMM-009 | D-01/D-03/D-08 | 高精度错别字/漏字/病句词表（其余交 AI 语义复核） |
| CMM-010 | C-15 | 金额数字格式（全角逗号、句点分隔、双小数点、千分位错位、小数位） |
| CMM-011 | C-14 | 金额单位应为“万元”（漏写“万”字、整数元金额） |
| CMM-012 | B-05 | 年度表述（畸形年份、比较年度写成本年度、报告语境年度错误） |

全部只看叙述行（表格行、目录页跳过），引文用 ``unique_quote`` 取本页唯一片段，
满足证据守卫“原文在所称页面唯一命中”的要求。
"""
from __future__ import annotations

import re
from typing import List, Sequence, Tuple

from .checklist_support import (
    CJK,
    flat_span,
    issue_at,
    iter_pages,
    page_flat,
    page_narrative,
    page_has_table,
    report_kind,
    report_year,
)
from .rule_outcome import RuleDeferred
from .rules_v33 import Document, Issue, Rule

# ---------------------------------------------------------------------------
# CMM-002 扩展：重复与标点（清单 D-02 / D-03 / D-04）
# ---------------------------------------------------------------------------

#: 两字词组叠用只认财政语境高频词（“监测预测预警”里“测预测预”不是重复）。
_DOUBLED_WORDS = frozenset(
    "支出 收入 预算 决算 人员 培训 经费 项目 资金 单位 部门 财政 拨款 主要 用于 原因 增加 减少"
    " 公务 接待 合计 其中 本年 情况 说明 安排 基本 车辆 采购 政府 年度 绩效 目标".split()
)
_ADJACENT_REPEAT_RE = re.compile(rf"([{CJK}][{CJK}0-9A-Za-z“”\"（）()]{{1,15}}?)\1")
#: 冒号分隔的重复（“主要用于：主要用于：”）；逗号分隔只认介词/关联词开头的短语
#: （“与2024年度相比，与2024年度相比”），“未安排政府性基金预算，政府性基金预算
#: 支出情况……”这类主题复指不算重复。
_COLON_REPEAT_RE = re.compile(rf"([{CJK}][^\s，,：:；;。、]{{2,20}}?)[：:]\s*\1")
_COMMA_REPEAT_RE = re.compile(
    r"((?:与|比|较|同|对|在|按|根据|其中|主要|经|由)[^\s，,：:；;。、]{2,18}?)[，,]\s*\1"
)
#: 单字叠用：决算 V33-113 已覆盖虚词表，这里补“于/费”，预算侧补全虚词表。
_SINGLE_DOUBLE_ALWAYS = "于费"
_SINGLE_DOUBLE_BUDGET = "是的了在与和或及并经由按据对从向"
_SINGLE_DOUBLE_GUARD = r"(?<!现)(?<!实)(?<!正)(?<!参)(?<!不)(?<!未)(?<!收)"

_DUP_PUNCT_RE = re.compile(r"([，。！？；：])\1+")
_MIXED_PUNCT_RE = re.compile(r"([，。！？；：])\s*(?!\1)([，。！？；：])")
_ENGLISH_PERIOD_RE = re.compile(rf"(?<=[{CJK}）”])\.(?=\s*$)", re.M)
#: 文号年份缺右六角括号：“〔2019126号”（应为“〔2019〕126号”）。
_DOC_NUMBER_RE = re.compile(r"[〔\[［]\s*(?:19|20)\d{2}\s*\d{1,4}\s*号")
_ENUM_RIGHT_PAREN_RE = re.compile(r"(?:^|[\s。；;：:，,])(?:\d{1,2}|[a-zA-Z]|[一二三四五六七八九十]{1,3})$")
_HEADING_LINE_RE = re.compile(r"^\s*(?:[一二三四五六七八九十]+、|第[一二三四五六七八九十]+部分|[（(][一二三四五六七八九十]+[)）])")
_PAREN_WINDOW = 320


def _doubled_word_issues(rule: Rule, doc: Document, kind: str) -> List[Issue]:
    issues: List[Issue] = []
    singles = _SINGLE_DOUBLE_ALWAYS + (_SINGLE_DOUBLE_BUDGET if kind != "final" else "")
    single_re = re.compile(_SINGLE_DOUBLE_GUARD + f"([{singles}])\\1(?!\\1)")
    for page, text in iter_pages(doc):
        spans: List[Tuple[int, int, str]] = []
        for offset, line in page_narrative(doc, page, text):
            for match in _ADJACENT_REPEAT_RE.finditer(line):
                unit = match.group(1)
                cjk = re.findall(f"[{CJK}]", unit)
                if len(cjk) < 2 or len(set(unit)) == 1:
                    continue
                if len(unit) == 2 and unit not in _DOUBLED_WORDS:
                    continue
                spans.append((offset + match.start(), offset + match.end(), unit))
            for pattern in (_COLON_REPEAT_RE, _COMMA_REPEAT_RE):
                for match in pattern.finditer(line):
                    spans.append((offset + match.start(), offset + match.end(), match.group(1)))
            for match in single_re.finditer(line):
                if match.group(1) == "费" and line[match.end():match.end() + 1] in ("用", "率"):
                    continue
                spans.append((offset + match.start(), offset + match.end(), match.group(1)))
        seen: List[Tuple[int, int]] = []
        for start, end, unit in sorted(spans, key=lambda s: (s[0], -(s[1] - s[0]))):
            if any(start < e and s < end for s, e in seen):
                continue
            seen.append((start, end))
            issues.append(issue_at(
                rule, f"疑似重复词：“{unit}”连续出现（{text[start:end].strip()}），请删除多余部分。",
                page, text, start, end, severity="warn", checklist_item="D-02",
            ))
    return issues


def _punctuation_issues(rule: Rule, doc: Document, kind: str) -> List[Issue]:
    issues: List[Issue] = []
    for page, text in iter_pages(doc):
        for offset, line in page_narrative(doc, page, text):
            if kind != "final":
                # 决算侧由 V33-113 报重复/混用标点；CMM-002 原有“。。”“；。”保持不变。
                for match in _DUP_PUNCT_RE.finditer(line):
                    if match.group(0) == "。。":
                        continue
                    issues.append(issue_at(
                        rule, f"重复标点：“{match.group(0)}”，请保留一个。",
                        page, text, offset + match.start(), offset + match.end(),
                        severity="warn", checklist_item="D-04",
                    ))
                for match in _MIXED_PUNCT_RE.finditer(line):
                    pair = match.group(1) + match.group(2)
                    if pair in ("；。", ";。"):
                        continue
                    issues.append(issue_at(
                        rule, f"标点连用：“{pair}”，请保留一个。",
                        page, text, offset + match.start(), offset + match.end(),
                        severity="warn", checklist_item="D-04",
                    ))
            for match in _ENGLISH_PERIOD_RE.finditer(line):
                issues.append(issue_at(
                    rule, "句末使用了英文句点“.”，应为中文句号“。”。",
                    page, text, offset + max(0, match.start() - 12), offset + match.end(),
                    severity="info", checklist_item="D-04",
                ))
            for match in _DOC_NUMBER_RE.finditer(line):
                issues.append(issue_at(
                    rule, f"文号格式有误：“{match.group(0)}”，年份应以六角括号“〔〕”括起（如〔2019〕126号）。",
                    page, text, offset + match.start(), offset + match.end(),
                    severity="info", checklist_item="D-04",
                ))
    issues.extend(_paren_balance_issues(rule, doc))
    return issues


def _paren_balance_issues(rule: Rule, doc: Document) -> List[Issue]:
    """括号配对：跨行、跨页按叙述流配对；新章节标题或超过窗口仍未闭合才报。

    逐段配对会把“（其中，……0个。绩效自评……0个）。”这类跨句括注误判成
    不配对，所以按叙述流走：右括号前若是“1）”这类序号写法不算缺左括号。
    """
    issues: List[Issue] = []
    stack: List[Tuple[int, int, int]] = []  # (page, page_offset, stream_pos)
    stream_pos = 0
    texts = {page: text for page, text in iter_pages(doc)}

    def _flush_open() -> None:
        for page, pos, _ in stack:
            text = texts[page]
            issues.append(issue_at(
                rule, "括号未闭合：左括号“（”后缺少对应的右括号“）”。",
                page, text, pos, pos + 1, severity="info", checklist_item="D-04",
            ))
        stack.clear()

    for page, text in texts.items():
        if page_has_table(doc, page - 1):
            # 表格页的单元格折行（“…中心（上海市”/“保险)”）无法按叙述流配对，整页跳过。
            _flush_open()
            continue
        for offset, line in page_narrative(doc, page, text):
            if _HEADING_LINE_RE.match(line):
                _flush_open()
            for index, char in enumerate(line):
                position = stream_pos + index
                if stack and position - stack[0][2] > _PAREN_WINDOW:
                    _flush_open()
                if char in "（(":
                    stack.append((page, offset + index, position))
                elif char in "）)":
                    if stack:
                        stack.pop()
                        continue
                    before = line[:index].rstrip()
                    if _ENUM_RIGHT_PAREN_RE.search(before) or not before:
                        continue
                    issues.append(issue_at(
                        rule, "括号未配对：右括号“）”前缺少对应的左括号“（”。",
                        page, text, offset + index, offset + index + 1,
                        severity="info", checklist_item="D-04",
                    ))
            stream_pos += len(line) + 1
    _flush_open()
    return issues


def checklist_text_anomaly_issues(rule: Rule, doc: Document) -> List[Issue]:
    """CMM-002 的清单扩展入口（重复与标点）。"""
    kind = report_kind(doc)
    return _doubled_word_issues(rule, doc, kind) + _punctuation_issues(rule, doc, kind)


# ---------------------------------------------------------------------------
# CMM-008 模板残留与编辑批注（清单 D-05 / D-06 / A-03 / A-12）
# ---------------------------------------------------------------------------

_TEMPLATE_PATTERNS: Sequence[Tuple[re.Pattern[str], str]] = (
    # “增加（减少）/减少（增加）”由 CMM-005 报告，这里只收其余二选一写法。
    (re.compile(r"(?:增长|下降)\s*[（(]\s*(?:下降|增长|减少|增加)\s*[)）]"
                r"|(?:增加|减少)\s*[（(]\s*(?:增长|下降|持平)\s*[)）]"
                r"|(?:大于|小于|高于|低于)\s*[（(]\s*(?:小于|大于|低于|高于|等于)\s*[)）]"),
     "二选一模板语未处理"),
    (re.compile(r"(?<=万元)\s*[（(]\s*(?:持平|增加|减少|增长|下降)\s*[)）]"), "金额后保留了模板括注"),
    (re.compile(r"[（(]\s*(?:下降|增长|增加|减少)\s*[)）]\s*[（(]\s*(?:下降|增长|增加|减少)\s*[)）]"),
     "二选一模板语并列出现"),
    (re.compile(r"[（(]\s*(?:增加|删除|修改|新增|补充)内容\s*[)）]"), "残留编辑批注"),
    (re.compile(r"由(?:单位|部门)根据三定方案|根据三定方案等相关规定中有关|中有关主要职能填写"), "残留模板提示语"),
    (re.compile(r"[（(][^（）()\n]{0,40}?(?:绩效目标管理工作情况|说明项目的总体情况|立项目的等|编报绩效目标的项目数量)"
                r"[^（）()\n]{0,60}[)）]"), "残留模板提示语"),
    (re.compile(r"[（(]\s*(?:简要)?(?:填写|说明)[^（）()\n]{2,40}[)）]"), "残留模板提示语"),
)
#: “部门（单位）”只在指代本报告主体时算残留；“批复部门（单位）的年度预决算”、
#: 名词解释里的泛指都是正常用语。
_SCOPE_TEMPLATE_RE = re.compile(
    r"(?P<pre>本|年[，,]?|年度|局|委|会|办公室|中心|部|院|所|馆|站|队|处|室|校|园|街道办事处|政府)?"
    r"(?P<hit>部门\s*[（(]\s*单位\s*[)）]|单位\s*[（(]\s*部门\s*[)）])"
    r"(?P<post>共有|收支|政府采购|面向中小企业|一般公共预算财政拨款|预算公开表|决算公开表|无机关运行经费|编报绩效)?"
)
_X_DIGIT_RE = re.compile(r"(?<![A-Za-z0-9])[XxＸｘ×]\s?[0-9](?=\s*(?:辆|台|个|人|家|万元|元|%|项|次|批|套))")


class CMM008_TemplateResidue(Rule):
    code, severity = "CMM-008", "warn"
    desc = "模板残留、编辑批注与占位误植（预/决算通用，清单 D-05/D-06）"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        for page, text in iter_pages(doc):
            for offset, line in page_narrative(doc, page, text):
                hits: List[Tuple[int, int, str]] = []
                for pattern, label in _TEMPLATE_PATTERNS:
                    for match in pattern.finditer(line):
                        hits.append((match.start(), match.end(), label))
                for match in _SCOPE_TEMPLATE_RE.finditer(line):
                    if match.group("pre") or match.group("post"):
                        hits.append((match.start("hit"), match.end("hit"), "部门/单位二选一模板语未处理"))
                for match in _X_DIGIT_RE.finditer(line):
                    hits.append((match.start(), match.end(), "疑似误植字符"))
                taken: List[Tuple[int, int]] = []
                for start, end, label in sorted(hits, key=lambda h: (h[0], -(h[1] - h[0]))):
                    if any(start < e and s < end for s, e in taken):
                        continue
                    taken.append((start, end))
                    phrase = line[start:end].strip()
                    issues.append(issue_at(
                        self, f"{label}：“{phrase}”，请按实际情况择一保留或删除。",
                        page, text, offset + start, offset + end,
                        severity=("info" if (re.search(r"(?:预算|决算)公开表", line[end:end + 8])
                                             or (label.startswith("部门/单位") and _HEADING_LINE_RE.match(line)))
                                  else "warn"),
                        checklist_item="D-05" if label != "疑似误植字符" else "D-06",
                    ))
        return issues


# ---------------------------------------------------------------------------
# CMM-009 错别字/漏字/病句高精度词表（清单 D-01 / D-03 / D-08）
# ---------------------------------------------------------------------------

#: (正则, 问题描述, 建议)。只收财政公开材料里确定无歧义的写法，其余交 AI 语义复核。
_TYPO_LEXICON: Sequence[Tuple[re.Pattern[str], str, str]] = (
    (re.compile(r"咨询付(?![费款])"), "错别字", "“咨询付”应为“咨询费”"),
    (re.compile(r"(?:补贴|资金|工资|奖金|补助|津贴|经费|抚恤金)放发|放发(?=补贴|资金|工资|奖金|补助|津贴|经费|抚恤金)"),
     "错别字", "“放发”应为“发放”"),
    (re.compile(r"科技技术(?=普及|研究|服务)"), "错别字", "“科技技术”应为“科学技术”"),
    (re.compile(r"公务车(?=购置|运行|用车)"), "错别字", "应为“公务用车”"),
    (re.compile(r"(?<=预算)拨(?=支出)"), "漏字", "“预算拨支出”漏“款”字，应为“预算拨款支出”"),
    (re.compile(r"(?<![行])政事业单位(?=养老|医疗|离退休)"), "漏字", "“政事业单位”漏“行”字"),
    (re.compile(r"(?<=其他)行政事业(?=养老|医疗)"), "漏字", "“行政事业养老/医疗”漏“单位”二字"),
    (re.compile(r"(?:事业|拨款|其他|经营|补助|上缴)收(?=\s*\d[\d,]*(?:\.\d+)?\s*万?元)"), "漏字", "“收”后漏“入”字"),
    (re.compile(r"主要用(?=由|人口|扶持|发放|支付|保障|开展|购买|购置|补助)"), "漏字", "“主要用”后漏“于”字"),
    (re.compile(r"主要用于\s*[:：]?\s*无\s*[,，。；;]"), "语句不通", "“主要用于无”语义不通，请改写"),
)


class CMM009_TypoLexicon(Rule):
    code, severity = "CMM-009", "warn"
    desc = "错别字、漏字与病句（高精度词表，预/决算通用，清单 D-01/D-03/D-08）"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        for page, text in iter_pages(doc):
            flat, positions = page_flat(doc, page, text)
            for pattern, kind, advice in _TYPO_LEXICON:
                for match in pattern.finditer(flat):
                    start, end = flat_span(positions, match.start(), match.end())
                    issues.append(issue_at(
                        self, f"{kind}：{advice}。", page, text, start, end,
                        severity="warn", checklist_item="D-01" if kind == "错别字" else "D-03",
                    ))
        return issues


# ---------------------------------------------------------------------------
# CMM-010 金额数字格式（清单 C-15）
# ---------------------------------------------------------------------------

_UNIT = r"\s*(?:万元|亿元|元)"
_FORMAT_PATTERNS: Sequence[Tuple[re.Pattern[str], str]] = (
    (re.compile(r"(?<![\d.,，])\d+\.\d{1,3}\.\d+" + _UNIT), "用句点作千分位分隔"),
    (re.compile(r"(?<![\d.])\d[\d,]*\.\d+\." + _UNIT), "金额后多一个点号"),
    (re.compile(r"(?<![\d.])\d+\.\.\d+"), "双小数点"),
    (re.compile(r"(?<![\d.])\d{1,3}[，﹐]\d{3}(?:[，﹐,]\d{3})*(?:\.\d+)?" + _UNIT), "千分位使用了中文全角逗号"),
    (re.compile(r"(?<![\d,，.])\d{1,3}(?:,\d{3})*,(?:\d{1,2}|\d{4,})(?:\.\d+)?" + _UNIT), "千分位错位"),
    (re.compile(r"(?<![\d,，.])\d{4,}(?:,\d{3})+(?:\.\d+)?" + _UNIT), "千分位错位"),
)
_MANY_DECIMALS_RE = re.compile(r"(?<![\d.])\d[\d,]*\.\d{3,}(?=\s*(?:万元|亿元))")
#: 预算侧百分比小数位（清单 C-09）；决算侧由 V33-232 报告。
_PERCENT_DECIMALS_RE = re.compile(r"(?<![\d.])\d+\.\d{3,}(?=\s*[%％])")


class CMM010_AmountFormat(Rule):
    code, severity = "CMM-010", "warn"
    desc = "金额数字格式规范（预/决算通用，清单 C-15）"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        decimals: List[Tuple[int, str, int, int]] = []
        for page, text in iter_pages(doc):
            flat, positions = page_flat(doc, page, text)
            taken: List[Tuple[int, int]] = []
            for pattern, label in _FORMAT_PATTERNS:
                for match in pattern.finditer(flat):
                    span = (match.start(), match.end())
                    if any(span[0] < e and s < span[1] for s, e in taken):
                        continue
                    taken.append(span)
                    start, end = flat_span(positions, *span)
                    issues.append(issue_at(
                        self, f"金额数字格式不规范（{label}）：“{match.group(0).strip()}”，"
                        "应使用半角逗号作千分位、保留一个小数点（如 7,985.49万元）。",
                        page, text, start, end, severity="warn", checklist_item="C-15",
                    ))
            for match in _MANY_DECIMALS_RE.finditer(flat):
                if not any(match.start() < e and s < match.end() for s, e in taken):
                    start, end = flat_span(positions, match.start(), match.end())
                    decimals.append((page, text, start, end))
        if report_kind(doc) == "budget":
            percents = []
            for page, text in iter_pages(doc):
                flat, positions = page_flat(doc, page, text)
                for match in _PERCENT_DECIMALS_RE.finditer(flat):
                    percents.append((page, text, *flat_span(positions, match.start(), match.end())))
            if percents:
                page, text, start, end = percents[0]
                issues.append(issue_at(
                    self, f"百分比小数位不统一：全文共 {len(percents)} 处百分比超过两位小数（如“{text[start:end]}%”），"
                    "应统一保留两位小数。", page, text, start, end, severity="info", checklist_item="C-09",
                    occurrences=len(percents),
                ))
        if decimals:
            # 整篇统一按 4~6 位小数书写的材料逐处报会淹没其他问题，汇总成一条。
            page, text, start, end = decimals[0]
            sample = text[start:end]
            issues.append(issue_at(
                self, f"金额小数位不统一：全文共 {len(decimals)} 处万元金额超过两位小数（如“{sample}万元”），"
                "应统一四舍五入保留两位小数。",
                page, text, start, end, severity="info", checklist_item="C-15",
                occurrences=len(decimals),
            ))
        return issues


# ---------------------------------------------------------------------------
# CMM-011 金额单位应为万元（清单 C-14，AGENTS.md R001）
# ---------------------------------------------------------------------------

_YUAN_AMOUNT_RE = re.compile(r"(?<![\d.,，])(\d[\d,]*(?:\.\d+)?)\s*元(?![/／每])")
_YUAN_RATE_CONTEXT_RE = re.compile(r"(?:单价|标准|每人|每天|每月|每年|人均|每次|每台|补助标准|收费)\S{0,6}$")


class CMM011_AmountUnitWan(Rule):
    code, severity = "CMM-011", "warn"
    desc = "说明正文金额单位应为万元（预/决算通用，清单 C-14 / R001）"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        integer_hits: List[Tuple[int, str, int, int]] = []
        for page, text in iter_pages(doc):
            flat, positions = page_flat(doc, page, text)
            for match in _YUAN_AMOUNT_RE.finditer(flat):
                before = flat[max(0, match.start() - 12):match.start()]
                if re.search(r"[万亿千百]$", before) or _YUAN_RATE_CONTEXT_RE.search(before):
                    continue
                raw = match.group(1).replace(",", "")
                try:
                    value = float(raw)
                except ValueError:
                    continue
                if value == 0:
                    continue
                start, end = flat_span(positions, match.start(), match.end())
                if "." in raw:
                    issues.append(issue_at(
                        self, f"金额单位疑似漏写“万”字：“{match.group(0).strip()}”，说明正文金额应以万元为单位。",
                        page, text, start, end, severity="warn", checklist_item="C-14",
                    ))
                else:
                    integer_hits.append((page, text, start, end))
        if integer_hits:
            page, text, start, end = integer_hits[0]
            issues.append(issue_at(
                self, f"说明正文共 {len(integer_hits)} 处金额以“元”为单位（如“{text[start:end].strip()}”），"
                "按规范应统一换算为万元并保留两位小数。",
                page, text, start, end, severity="info", checklist_item="C-14",
                occurrences=len(integer_hits),
            ))
        return issues


# ---------------------------------------------------------------------------
# CMM-012 年度表述（清单 B-05）
# ---------------------------------------------------------------------------

_FIVE_DIGIT_YEAR_RE = re.compile(r"(?<!\d)20\d{3}(?=年)")
_THREE_DIGIT_YEAR_RE = re.compile(r"(?<![\d.])20\d(?=年度?(?:部门|单位)?(?:预算|决算|“|\"))")
_COMPARE_RE = re.compile(r"(?:比|较|与|同)(20\d{2})年(度)?(年初|全年|调整|预算|决算|执行|相比|同期|比较|实际|增加|减少|增长|下降)?")
_FINAL_CONTEXT_RE = re.compile(
    r"(?<![比较与同至])(20\d{2})年度?(?:部门|单位)?(?:决算(?:编制范围|表|情况说明|报告)"
    r"|无(?:政府性基金|国有资本经营)预算|[“\"]?三公[”\"]?经费(?:财政拨款)?支出决算)"
)
_BUDGET_CONTEXT_RE = re.compile(
    r"(?<![比较与同至])(20\d{2})年(?:本部门|本单位)?(?:政府采购预算|[“\"]?三公[”\"]?经费预算|机关运行经费预算"
    r"|(?:单位|部门)?预算安排购置车辆)"
)


class CMM012_YearExpression(Rule):
    code, severity = "CMM-012", "warn"
    desc = "年度表述与报告年度一致（预/决算通用，清单 B-05）"

    def apply(self, doc: Document) -> List[Issue]:
        kind = report_kind(doc)
        year = report_year(doc)
        issues: List[Issue] = []
        for page, text in iter_pages(doc, skip_toc=False):
            flat, positions = page_flat(doc, page, text)

            def _emit(match: "re.Match[str]", message: str, severity: str = "warn",
                      *, _page=page, _text=text, _positions=positions) -> None:
                start, end = flat_span(_positions, match.start(), match.end())
                issues.append(issue_at(self, message, _page, _text, start, end,
                                       severity=severity, checklist_item="B-05"))

            for match in _FIVE_DIGIT_YEAR_RE.finditer(flat):
                _emit(match, f"年份书写有误：“{match.group(0)}年”不是有效年份，疑似笔误。")
            for match in _THREE_DIGIT_YEAR_RE.finditer(flat):
                _emit(match, f"年份书写有误：“{match.group(0)}年”缺位，疑似漏写数字。")
            if year is None or kind not in {"budget", "final"}:
                continue
            for match in _COMPARE_RE.finditer(flat):
                if int(match.group(1)) != year:
                    continue
                follower = match.group(3) or ""
                if kind == "final" and follower in {"年初", "全年", "调整", "预算"}:
                    continue  # 决算数与本年度预算数对比是正常口径
                _emit(match, f"比较年度有误：本报告为{year}年度，“{match.group(0)}”的比较年度应为{year - 1}年。")
            context_re = _FINAL_CONTEXT_RE if kind == "final" else _BUDGET_CONTEXT_RE
            for match in context_re.finditer(flat):
                if int(match.group(1)) == year:
                    continue
                _emit(match, f"年度表述与报告年度不一致：本报告为{year}年度，正文写作“{match.group(0)}”。")
        if year is None and kind in {"budget", "final"}:
            raise RuleDeferred(
                self.code, detail="未识别到报告年度，比较年度与报告语境年度无法核对",
                partial_issues=issues, unresolved_reasons=["未识别到报告年度"],
            )
        return issues


__all__ = [
    "CMM008_TemplateResidue",
    "CMM009_TypoLexicon",
    "CMM010_AmountFormat",
    "CMM011_AmountUnitWan",
    "CMM012_YearExpression",
    "checklist_text_anomaly_issues",
]
