"""决算公开中有原文证据的车辆购置矛盾及编制模板残留。

车辆购置数量与现金支出并非天然相等；这里只提示相邻的零支出与肯定
购车用途表述，明确付款时点、无偿调拨等情况则交人工确认口径。
"""
from __future__ import annotations

import re
from decimal import Decimal

from .native_narrative_checks import (
    _NUMBER, _Text, _UNIT, _field, _metadata, _receipt, _wan,
    _year_and_owner_reason,
)


_PURCHASE_SPEND = re.compile(
    rf"公务用车购置(?:费)?(?:支出(?:决算(?:数)?)?|决算(?:数)?)(?:为|是|共计)?"
    rf"(?P<amount>{_NUMBER})(?P<unit>{_UNIT})"
)
_COUNT = r"(?:[1-9]\d*|[一二三四五六七八九十百]+)"
_PURCHASE_USE = re.compile(
    rf"主要用于(?:{_COUNT}辆(?:公务用车|公务车辆)(?:的)?购置"
    rf"|(?:购置|购买)(?:公务用车|公务车辆)?{_COUNT}辆(?:公务用车|公务车辆)?)"
    rf"|(?:购置|购买)(?:了)?(?:公务用车|公务车辆){_COUNT}辆"
)
_NONCURRENT = re.compile(r"拟|计划|预计|预算安排|购置计划|用于以后年度|上年购置|以前年度购置")
_PAYMENT_OR_TRANSFER = re.compile(
    r"尚未(?:付款|支付|结算)|未(?:付款|支付|结算)|未发生(?:付款|支付)|尚未发生(?:付款|支付)"
    r"|(?:无偿|免费)(?:调拨|划拨|取得|获得)|跨年(?:度)?支付|以后年度支付|次年支付"
    r"|由[^。；;]{1,35}(?:代付|付款|支付)|非本部门(?:付款|支付)|非本单位(?:付款|支付)"
)
_NO_UPDATE = re.compile(
    r"(?P<year>20\d{2})?(?:年度|年|本年度|本年|年内)?(?:无|没有|未)"
    r"(?:公务(?:用)?车辆?|车辆?)(?:更新|购置)"
)
_NEXT_METRIC = re.compile(r"公务用车运行|公务接待|因公出国|[（(][一二三四五六七八九十]+[）)]")
_TEMPLATE = re.compile(
    r"无(?P<fund>政府性基金|国有资本经营)预算财政拨款(?:收入和支出|收入支出|收入、支出)的"
    r"[，,]?仍保留该表格式[，,]?并在本表下方作(?:下述|如下)说明[:：]"
)


def _major_scopes(doc, text):
    from .rules_v33 import _txt_fund_merged_pages, _nar_repeat_sections
    merged, offsets = _txt_fund_merged_pages(doc)
    for title, start, end in _nar_repeat_sections(merged, doc, offsets):
        if "三公" in title:
            left, right = text.compact_range(start, end)
            yield title, left, right


def _check_vehicle_purchase(rule, doc, text, year, owner, issues, unresolved):
    for title, start, end in _major_scopes(doc, text):
        block = text.compact[start:end]
        for spend in _PURCHASE_SPEND.finditer(block):
            amount = _wan(spend["amount"], spend["unit"])
            if amount != Decimal("0"):
                continue
            # The disclosed购置支出 and its immediately adjacent用途 form one
            # semantic item. Stop before运行费/接待费, never scan an entire
            # section's purchases for a zero expenditure match.
            stop = min(len(block), spend.end() + 240)
            next_metric = _NEXT_METRIC.search(block, spend.end(), stop)
            if next_metric:
                stop = next_metric.start()
            tail = block[spend.end():stop]
            sentence_ends = list(re.finditer(r"[。；;]", tail))
            if len(sentence_ends) >= 3:
                stop = spend.end() + sentence_ends[2].end()
                tail = block[spend.end():stop]
            purchase = _PURCHASE_USE.search(tail)
            if not purchase:
                continue
            clause_start = max(tail.rfind(ch, 0, purchase.start()) for ch in "。，,；;") + 1
            if re.search(r"(?:未|没有|并未|不曾|不予)(?:实际)?$", tail[clause_start:purchase.start()]):
                continue
            proof_start = max(block.rfind("。", 0, spend.start()), block.rfind("；", 0, spend.start())) + 1
            # Include the complete purchase sentence, including any payment
            # qualification after the quantity. Truncating at the match would
            # falsely flag a valid尚未付款 disclosure.
            purchase_stop = spend.end() + purchase.end()
            sentence_stop = re.search(r"[。；;]", block[purchase_stop:stop])
            proof_end = purchase_stop + sentence_stop.end() if sentence_stop else stop
            proof = block[proof_start:proof_end]
            if _NONCURRENT.search(proof):
                continue
            relation = f"zero_vehicle_purchase_semantics:p{start + spend.start()}"
            formula = "zero purchase expenditure and positive purchase-use wording must explain their accounting basis"
            reason = _year_and_owner_reason(proof, block[proof_start:spend.start()], year, owner)
            qualifier = _PAYMENT_OR_TRANSFER.search(proof + block[proof_end:stop])
            if qualifier:
                reason = "原文存在未付款、跨年度支付或无偿取得等说明，购置数量与支出口径需人工确认"
                proof_end = stop
            refs = text.refs(start + proof_start, start + proof_end, "零购置支出及购车用途原文")
            # A明确同年度无更新 statement is supporting evidence; it never
            # supplies a missing current amount or overwrites source facts.
            prior = block[max(0, spend.start() - 900):spend.start()]
            no_updates = list(_NO_UPDATE.finditer(prior))
            if no_updates:
                no_update = no_updates[-1]
                if not no_update["year"] or (year and int(no_update["year"]) == year):
                    offset = max(0, spend.start() - 900)
                    refs += text.refs(start + offset + no_update.start(), start + offset + no_update.end(),
                                      "本年无车辆更新说明")
            location = {"page": refs[0]["page"], "pages": sorted({ref["page"] for ref in refs}),
                        "section": title, "relation_type": "zero_vehicle_purchase_semantics",
                        "purchase_spend_wan": str(amount), "table_refs": refs,
                        "expected": "说明未购置车辆，或补充购置数量与现金支付、资金来源、取得方式的口径",
                        "actual": proof}
            quote = "\n".join(ref["span"] for ref in refs)
            if reason:
                detail = f"第{location['page']}页车辆购置语义关系未完成：{reason}"
                unresolved.append(detail)
                _receipt(rule, doc, relation, "insufficient_data", formula, detail)
                issues.append(rule._issue(
                    f"车辆购置支出为零且说明购车用途，{reason}；建议补充取得方式和付款年度，不能据此认定金额错误。",
                    location, "manual_review", evidence_text=quote, check_relation_id=f"{rule.code}:{relation}"))
            else:
                _receipt(rule, doc, relation, "fail", formula)
                issues.append(rule._issue(
                    "公务用车购置支出为0万元，但相邻说明写有实际购车用途，文字口径不自洽；"
                    "建议核对车辆购置及付款口径，未购置的应删除购车用途模板。",
                    location, "medium", evidence_text=quote, check_relation_id=f"{rule.code}:{relation}"))


def _check_template(rule, doc, text, issues):
    refs, funds = [], []
    for match in _TEMPLATE.finditer(text.compact):
        refs.extend(text.refs(match.start(), match.end(), "条件表编制操作提示原文"))
        funds.append(match["fund"])
    if not refs:
        return
    # The same boilerplate in two conditional tables is one editing problem,
    # with both original pages retained. Existing无此项说明 stays valid.
    relation = "conditional_template_instruction_residue"
    _receipt(rule, doc, relation, "fail", "public conditional tables should omit authoring instructions")
    location = {"page": refs[0]["page"], "pages": sorted({ref["page"] for ref in refs}),
                "section": "；".join(dict.fromkeys(f"{fund}预算财政拨款收入支出决算表" for fund in funds)),
                "relation_type": relation, "table_refs": refs,
                "expected": "删除给编制者的操作提示，保留空表及事实性的无收入支出说明"}
    issues.append(rule._issue(
        "条件性空表仍保留“仍保留该表格式，并在本表下方作下述说明”等编制操作提示；"
        "建议清理模板提示，保留无收入支出的事实说明。本问题不表示缺表或缺少空表说明。",
        location, "low", evidence_text="\n".join(ref["span"] for ref in refs),
        check_relation_id=f"{rule.code}:{relation}"))


def check_disclosure_semantics(rule, doc):
    """Return (issues, unresolved); incomplete accounting scope is never pass."""
    kind = _field(getattr(getattr(doc, "document_profile", None), "report_kind", None)) or getattr(doc, "report_kind", None)
    if kind in {"budget", "dept_budget", "gov_budget"}:
        return [], []
    text = _Text.from_pages(getattr(doc, "page_texts", None) or [])
    year, owner, _ = _metadata(doc, text)
    issues, unresolved = [], []
    _check_vehicle_purchase(rule, doc, text, year, owner, issues, unresolved)
    _check_template(rule, doc, text, issues)
    return issues, list(dict.fromkeys(unresolved))
