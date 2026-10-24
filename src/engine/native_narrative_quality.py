"""有直接文字与数字证据的决算说明质量提示。

原因是否充分只判断可证明的同义重述，不用原因长度判断。归一匹配后仍
返回未经改写的逐物理页原文；没有数值或口径证据的关系记为未完成。
"""
from __future__ import annotations

import re
from decimal import Decimal

from .rule_policy import amount_difference_exceeds_policy
from .native_narrative_checks import (
    _CALIBER_CHANGE, _NUMBER, _Text, _UNIT, _field, _metadata,
    _receipt, _sections, _wan, _year_and_owner_reason,
)


_REASON = re.compile(r"(?:主要原因|原因)(?:是|为|[:：])(?P<body>[^。；;]{1,140})[。；;]")
_RESULT_ONLY = re.compile(
    r"(?:(?:决算数与预算数|预算数与决算数)(?:基本)?持平"
    r"|(?:该项)?(?:实际)?(?:支出|支出决算数|决算数)(?:小于|大于|低于|高于)(?:年初)?预算数"
    r"|(?:机关运行(?:经费)?支出|机关运行经费|经费支出|支出)(?:同比)?"
    r"(?:略有|有所|略)?(?:增加|减少|增多|下降))"
)
_BUDGET_PAIR = re.compile(
    rf"年初预算(?:数)?(?:为|是)?(?P<budget>{_NUMBER})(?P<budget_unit>{_UNIT})"
    rf"(?P<middle>[^。；;]{{0,120}})支出决算(?:数)?(?:为|是)?"
    rf"(?P<actual>{_NUMBER})(?P<actual_unit>{_UNIT})"
)
_OPERATING_CURRENT = re.compile(
    rf"机关运行经费支出(?:决算(?:数)?)?(?:为|是|共计)?"
    rf"(?P<current>{_NUMBER})(?P<unit>{_UNIT})"
)
_OPERATING_CHANGE = re.compile(
    rf"(?:与|较|比)(?P<previous_year>20\d{{2}}|上年|去年)(?:年度|年)?(?:相比)?[，,]?"
    rf"(?:机关运行经费支出)?(?P<direction>增加|减少)"
    rf"(?P<change>{_NUMBER})(?P<unit>{_UNIT})"
    rf"(?:[，,](?:增长|下降|增加|减少){_NUMBER}[%％])?"
)


def _scopes(doc, text):
    from .rules_v33 import _txt_fund_merged_pages, _nar_repeat_sections
    merged, offsets = _txt_fund_merged_pages(doc)
    scopes = []
    for title, start, end in _nar_repeat_sections(merged, doc, offsets):
        if (("一般公共预算" in title and "基本支出" not in title)
                or "其他重要事项" in title or "机关运行" in title):
            left, right = text.compact_range(start, end)
            scopes.append((title, left, right))
    # A standalone机关运行 subsection is also reviewable; do not repeat the
    # check if an enclosing其他重要事项 section already contains it.
    for title, start, end in _sections(text):
        if "机关运行" in title and not any(left <= start < right for _, left, right in scopes):
            scopes.append((title, start, end))
    return scopes, merged, offsets


def _cause_body(value):
    return re.sub(r"^根据年度实际情况[,，]?", "", value)


def _bound_prefix(block, reason_start):
    """A previous reason, numbered entry or subsection terminates operand scope."""
    left = max(0, reason_start - 500)
    previous = list(_REASON.finditer(block, left, reason_start))
    if previous:
        left = max(left, previous[-1].end())
    entries = list(re.finditer(r"(?:\d{1,2}[、]|[（(][一二三四五六七八九十]+[）)])", block[left:reason_start]))
    if entries:
        left += entries[-1].end()
    return left, block[left:reason_start]


def _quality_reasons(rule, doc, text, title, start, end, year, owner, issues, unresolved):
    block = text.compact[start:end]
    for reason in _REASON.finditer(block):
        if not _RESULT_ONLY.fullmatch(_cause_body(reason["body"])):
            continue
        relation = f"reason_restates_result:p{start + reason.start()}"
        formula = "stated nonzero budget difference or year-on-year change requires a cause"
        left, prefix = _bound_prefix(block, reason.start())
        pairs = list(_BUDGET_PAIR.finditer(prefix))
        match, amounts, kind = None, {}, None
        if pairs:
            pair = pairs[-1]
            # An intervening分项 or another amount prevents assigning a nearby
            # budget/actual pair to this cause.
            bridge = prefix[pair.end():]
            if ("其中" not in pair["middle"] and "其中" not in bridge
                    and not re.search(rf"{_NUMBER}{_UNIT}", bridge)
                    and bridge.count("。") <= 1 and not re.search(r"[；;]", bridge)):
                budget = _wan(pair["budget"], pair["budget_unit"])
                actual = _wan(pair["actual"], pair["actual_unit"])
                if not amount_difference_exceeds_policy(actual - budget):
                    continue
                match, kind = pair, "budget_difference"
                amounts = {"budget_amount_wan": str(budget), "actual_amount_wan": str(actual)}
        if match is None and "机关运行" in prefix:
            changes = list(_OPERATING_CHANGE.finditer(prefix))
            currents = list(_OPERATING_CURRENT.finditer(prefix))
            if changes and currents:
                change, current = changes[-1], currents[-1]
                bridge = prefix[current.end():change.start()]
                tail = prefix[change.end():]
                if (current.end() <= change.start() and bridge.count("。") <= 1
                        and tail.count("。") <= 1 and not re.search(r"其中|[；;]", bridge + tail)
                        and not re.search(rf"{_NUMBER}{_UNIT}", bridge + tail)):
                    change_amount = _wan(change["change"], change["unit"])
                    if change_amount == 0:
                        continue
                    previous_year = year - 1 if year and change["previous_year"] in {"上年", "去年"} else (
                        int(change["previous_year"]) if change["previous_year"].isdigit() else None)
                    if year and previous_year == year - 1:
                        match, kind = current, "year_on_year"
                        amounts = {"current_amount_wan": str(_wan(current["current"], current["unit"])),
                                   "change_amount_wan": str(change_amount), "direction": change["direction"],
                                   "current_year": year, "previous_year": previous_year}
        problem = None
        if match is None:
            problem = "仅重述结果的原因缺少同条明确的预算差额或同比增减金额，需人工确认"
            proof_start = start + left
        else:
            proof_start = start + left + match.start()
            proof_prefix = prefix[:match.start()]
            problem = _year_and_owner_reason(prefix + reason.group(), proof_prefix, year, owner)
            if _CALIBER_CHANGE.search(prefix + reason.group()):
                problem = "差异采用调整口径，需人工确认其与原因说明的关系"
            if problem:
                proof_start = start + left
        if problem:
            refs = text.refs(proof_start, start + reason.end(), "原因及可见数字原文")
            page = refs[0]["page"] if refs else None
            detail = f"第{page}页原因质量关系未完成：{problem}"
            unresolved.append(detail)
            _receipt(rule, doc, relation, "insufficient_data", formula, detail)
            continue
        refs = text.refs(proof_start, start + reason.end(), "差异及原因原文")
        location = {"page": refs[0]["page"], "pages": sorted({ref["page"] for ref in refs}),
                    "section": title, "relation_type": "reason_restates_result", "cause_kind": kind,
                    **amounts, "table_refs": refs}
        _receipt(rule, doc, relation, "fail", formula)
        description = (f"年初预算{amounts['budget_amount_wan']}万元、决算{amounts['actual_amount_wan']}万元"
                       if kind == "budget_difference" else
                       f"机关运行经费同比{amounts['direction']}{amounts['change_amount_wan']}万元")
        issues.append(rule._issue(
            f"差异原因仅重述结果：{description}；“{reason['body']}”未说明产生差异的具体事项，建议补充说明。",
            location, "medium", evidence_text="\n".join(ref["span"] for ref in refs),
            check_relation_id=f"{rule.code}:{relation}"))


def _numbering_issues(rule, doc, merged, offsets):
    from .rules_v33 import _txt_fund_page_for, _nar_repeat_sections
    issues = []
    for title, start, end in _nar_repeat_sections(merged, doc, offsets):
        if "一般公共预算" not in title or "基本支出" in title:
            continue
        scope = merged[start:end]
        markers = list(re.finditer(r"(?:^|\n)\s*(?P<num>\d{1,2})[、.．](?!\d)", scope))
        if len(markers) < 4:
            continue
        for previous, current in zip(markers, markers[1:], strict=False):
            first, second = int(previous.group("num")), int(current.group("num"))
            if second <= first + 1:
                continue
            refs = []
            for marker in (previous, current):
                begin = marker.start("num")
                line_end = scope.find("\n", begin)
                line_end = len(scope) if line_end < 0 else line_end
                refs.append({"page": _txt_fund_page_for(offsets, start + begin),
                             "span": scope[begin:line_end], "role": "说明条目序号"})
            issues.append(rule._issue(
                f"说明条目序号由{first}跳到{second}，建议核对编号连续性；不能据此判定科目内容缺失。",
                {"page": refs[0]["page"], "pages": sorted({ref["page"] for ref in refs}),
                 "section": title, "relation_type": "item_number_gap", "table_refs": refs},
                "low", evidence_text="\n".join(ref["span"] for ref in refs)))
    return issues


def check_quality_relations(rule, doc):
    """Return (issues, unresolved); callers must propagate unresolved to coverage."""
    kind = _field(getattr(getattr(doc, "document_profile", None), "report_kind", None)) or getattr(doc, "report_kind", None)
    if kind in {"budget", "dept_budget", "gov_budget"}:
        return [], []
    text = _Text.from_pages(getattr(doc, "page_texts", None) or [])
    year, owner, _ = _metadata(doc, text)
    scopes, merged, offsets = _scopes(doc, text)
    issues, unresolved = [], []
    for title, start, end in scopes:
        _quality_reasons(rule, doc, text, title, start, end, year, owner, issues, unresolved)
    issues.extend(_numbering_issues(rule, doc, merged, offsets))
    return issues, list(dict.fromkeys(unresolved))


def check_quality(rule, doc):
    """Compatibility API; coverage-aware callers use check_quality_relations."""
    return check_quality_relations(rule, doc)[0]
