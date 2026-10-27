"""检查点清单：决算专用规则与预决算通用的空表说明反向检查（2026-10-22 全系统默认）。

| 规则 | 清单条目 | 内容 |
|------|----------|------|
| V33-251 | 决算 A-09/C-11/C-12 | 一般公共预算财政拨款支出决算具体情况：逐项细化到项、年初预算与支出决算分项加总等于总额、不漏列科目 |
| V33-252 | 决算 C-04 | 财政拨款收支总表“政府性基金/国有资本经营”列本年支出 = 对应决算表本年支出合计 |
| CMM-026 | 预算 A-07、决算 A-07 | 有数据的表不得保留“本表无数据/空表”说明 |
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .amount_math import classify_amount_diff
from .checklist_support import compact, display_envelope, flat_span, issue_at, iter_pages, page_flat, page_has_table
from .fact_context import _canonical_budget_stage
from .native_table_arithmetic import table_facts
from .rule_outcome import RuleDeferred, RuleNotApplicable
from .rules_v33 import Document, Issue, Rule
from src.utils.narration import normalize_amount_punctuation

_AMOUNT = r"(\d[\d,]*(?:\.\d+)?)"


def _dec(text: Any) -> Optional[Decimal]:
    try:
        return Decimal(str(text).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


# ---------------------------------------------------------------------------
# V33-251 支出决算具体情况
# ---------------------------------------------------------------------------

_DETAIL_START_RE = re.compile(r"(?:[（(]三[)）])?一般公共预算财政拨款支出决算具体情况")
_DETAIL_STOP_RE = re.compile(r"六、|一般公共预算财政拨款基本支出决算情况说明")
_DETAIL_TOTAL_RE = re.compile(
    r"年初预算(?:数)?(?:为|是)?\s*[:：]?\s*" + _AMOUNT + r"\s*万元[，,、\s]*"
    r"支出决算(?:数)?(?:为|是)?\s*[:：]?\s*" + _AMOUNT + r"\s*万元"
)
_ITEM_START_RE = re.compile(r"(?:^|(?<=[。；;：:]))(\d{1,2})[、.．](?=[一-鿿“\"])")
# 声明角色：年初预算及其省略“年初”的写法（只认句首/分句起点的“预算为”，
# 避免把“决算数大于预算数2.39万元”“追加预算”“调整后预算”当成预算声明），
# 支出决算、决算支出、单说“决算数”。单位可缺省——缺单位不是“没披露”，
# 而是已披露但不可归一，二者在完整性清单里必须分开。
_DECL_RE = re.compile(
    r"(?P<kind>年初预算|支出决算|决算支出|决算数|"
    r"(?:(?<=[。；;：:，,])[ \t]*预算))"
    r"(?:数)?(?:为|是)?\s*[:：]?\s*"
    r"(?P<amount>" + _AMOUNT + r")\s*(?P<unit>亿元|万元|元)?"
)
_AMOUNT_UNIT_FACTOR = {
    "亿元": Decimal("10000"),
    "万元": Decimal("1"),
    "元": Decimal("0.0001"),
}
_DECL_BUDGET_KINDS = {"年初预算", "预算"}
_SUBJECT_MARK_RE = re.compile(r"[（(]\s*项\s*[)）]")
_LEVEL_MARK_RE = re.compile(r"[（(]\s*(?:类|款|项)\s*[)）]")
_ITEM_NUM_RE = re.compile(r"^\d{1,2}[、.．]\s*")
_SUBJECT_SUFFIX_RE = re.compile(r"^[^，。；：,;:]{1,40}支出[。]?$")
_SUBJECT_DASH_RE = re.compile(r"^[^，。；：,;:]{1,40}支出\s*[—\-－]")
_NARRATIVE_MARK_RE = re.compile(r"(?:上述|该|本|此|各|相关|前述|以上|列入|除外)[\s“”\"']*$")
# 项级科目后直接跟的金额（“（项）39.99万元”“（项）支出843.41万元”）。
_HEADER_AMOUNT_RE = re.compile(
    r"[（(]\s*项\s*[)）]\s*(?:支出(?:数)?(?:为|是)?\s*[:：]?\s*)?"
    r"(?P<amount>" + _AMOUNT + r")\s*(?P<unit>亿元|万元|元)?"
)
# 依法不予公开不是缺披露：该槽位不纳入“必须披露”集合，但仍保留其他槽位检查。
_WITHHELD_RE = re.compile(r"依法不予公开|涉密[^。；;]{0,30}(?:不予公开|不公开)")
# 明确的无此项/零值说明；只认“没有这项金额”的说法，
# 不把“未提供/未披露”当成零（那正是缺披露）。
_ZERO_DECL = {
    "budget": re.compile(
        r"年初(?:无预算|预算不含此项|未安排预算|未编制预算|预算为零)|无年初预算|未安排年初预算"
    ),
    "final": re.compile(
        r"无(?:此|该项|本项)?(?:支出|决算)|未(?:发生|安排|列支|支出)(?:此|该项|本项)?支出"
    ),
}


def _detail_section(doc: Document):
    """跨页拼接“具体情况”段：返回 [(页, 原文, 平铺, 偏移, 起, 止)]。"""
    blocks = []
    active = False
    for page, text in iter_pages(doc):
        flat, positions = page_flat(doc, page, text)
        flat = normalize_amount_punctuation(flat)
        begin = 0
        if not active:
            match = _DETAIL_START_RE.search(flat)
            if not match:
                continue
            active, begin = True, match.end()
        stop = _DETAIL_STOP_RE.search(flat, begin)
        end = stop.start() if stop else len(flat)
        blocks.append((page, text, flat, positions, begin, end))
        if stop:
            break
    return blocks


def _decl_kind(kind: str) -> str:
    return "budget" if kind.strip() in _DECL_BUDGET_KINDS else "final"


def _is_subject_item(body: str) -> bool:
    """编号条目是否为项级科目条目；说明原因的编号列表不算应披露集合。"""
    head = re.split(r"主要用于|。", body, maxsplit=1)[0].strip()
    head = head.replace("“", "").replace("”", "").replace('"', "")
    head = _ITEM_NUM_RE.sub("", head)
    if _LEVEL_MARK_RE.search(head):
        return True
    if _SUBJECT_DASH_RE.match(head):
        return True
    return bool(_SUBJECT_SUFFIX_RE.match(head.rstrip("。")))


def _subject_clauses(joined: str, items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按真正的项级科目头拆槽位，不把“除列入上述（项）外”当成新科目。"""
    clauses: List[Dict[str, Any]] = []
    for item in items:
        start, stop = int(item["pos"]), int(item["stop"])
        body = joined[start:stop]
        if not _is_subject_item(body):
            continue
        accepted = []
        for mark in _SUBJECT_MARK_RE.finditer(body):
            boundary = accepted[-1].start() if accepted else 0
            sentence_tail = re.split(r"[。；;]", body[boundary:mark.start()])[-1]
            if _NARRATIVE_MARK_RE.search(sentence_tail):
                continue
            if (_LEVEL_MARK_RE.search(sentence_tail)
                    or re.search(r"支出\s*$", sentence_tail)
                    or _SUBJECT_DASH_RE.search(sentence_tail)):
                accepted.append(mark)
        if not accepted:
            clauses.append({"start": start, "end": stop, "label": body[:60]})
            continue
        for index, mark in enumerate(accepted):
            clause_start = 0 if index == 0 else mark.start()
            clause_end = accepted[index + 1].start() if index + 1 < len(accepted) else len(body)
            clauses.append({
                "start": start + clause_start,
                "end": start + clause_end,
                "label": body[max(0, mark.start() - 60):mark.start() + 40],
            })
    return clauses


def _clause_declaration_accounting(
    joined: str,
    clauses: Sequence[Dict[str, Any]],
    records: List[Dict[str, Any]],
    tail_start: int,
) -> Tuple[Dict[str, List[Dict[str, Any]]], set]:
    """逐科目核对年初预算/支出决算槽位；返回缺失槽位与候选冲突位置。

    只有“声明存在但读不出金额/单位”的条目才进 not_included；
    完全没有数字的槽位不能当作没有检查义务，单独记为 item_disclosure_missing。
    """
    missing: Dict[str, List[Dict[str, Any]]] = {"budget": [], "final": []}
    conflicts = set()
    for clause in clauses:
        start, end = int(clause["start"]), int(clause["end"])
        text_start = max(start, tail_start)
        text = joined[text_start:end]
        in_clause = [record for record in records if start <= record["pos"] < end]
        for kind in ("budget", "final"):
            kind_records = [record for record in in_clause if record["kind"] == kind]
            for sentence in re.finditer(r"[^。；;]+", text):
                low, high = text_start + sentence.start(), text_start + sentence.end()
                usable = [record for record in kind_records
                          if record["value_wan"] is not None and low <= record["pos"] < high]
                if len({(record["raw"], record["unit"]) for record in usable}) > 1:
                    for record in usable:
                        record["not_included_reason"] = "candidate_amount_conflict"
                        conflicts.add(record["pos"])
            if any(record["value_wan"] is not None and record["pos"] not in conflicts
                   for record in kind_records):
                continue
            if any(record["pos"] not in conflicts for record in kind_records):
                continue
            if _WITHHELD_RE.search(text):
                continue
            if _ZERO_DECL[kind].search(text):
                records.append({
                    "kind": kind, "raw": "0", "unit": "万元", "pos": start,
                    "value_wan": Decimal(0), "source": "explicit_zero",
                    "not_included_reason": None,
                })
                continue
            if kind == "final":
                bare = _HEADER_AMOUNT_RE.search(text)
                if bare:
                    raw, unit = bare.group("amount"), bare.group("unit")
                    value = _dec(raw)
                    factor = _AMOUNT_UNIT_FACTOR.get(unit)
                    records.append({
                        "kind": "final", "raw": raw, "unit": unit,
                        "pos": text_start + bare.start(),
                        "value_wan": (
                            value * factor if value is not None and factor is not None else None
                        ),
                        "source": "subject_header", "not_included_reason": None,
                    })
                    continue
            missing[kind].append({
                "kind": kind, "raw": "", "unit": None, "value_wan": None,
                "pos": start, "not_included_reason": "item_disclosure_missing",
                "item_head": clause["label"],
            })
    return missing, conflicts


def _completeness_summary(items: Sequence[Dict[str, Any]], label: str) -> str:
    missing = sum(1 for item in items
                  if item.get("not_included_reason") == "item_disclosure_missing")
    conflict = sum(1 for item in items
                   if item.get("not_included_reason") == "candidate_amount_conflict")
    unusable = len(items) - missing - conflict
    parts = []
    if missing:
        parts.append(f"{missing}处未披露{label}金额")
    if conflict:
        parts.append(f"{conflict}处{label}金额存在候选冲突")
    if unusable:
        parts.append(f"{unusable}处{label}金额缺少可归一单位或格式")
    return "、".join(parts) or "金额无法纳入合计"


def _completeness_codes(items: Sequence[Dict[str, Any]]) -> List[str]:
    codes: List[str] = []
    reasons = {item.get("not_included_reason") for item in items}
    if "item_disclosure_missing" in reasons:
        codes.append("item_disclosure_missing")
    if "candidate_amount_conflict" in reasons:
        codes.append("candidate_amount_conflict")
    if reasons - {"item_disclosure_missing", "candidate_amount_conflict", None}:
        codes.append("item_amount_not_included")
    return codes


class R33251_FunctionalDetailCompleteness(Rule):
    code, severity = "V33-251", "warn"
    desc = "支出决算具体情况逐项细化到项、分项加总等于总额、不漏列科目（清单 A-09/C-11/C-12）"

    def apply(self, doc: Document) -> List[Issue]:
        blocks = _detail_section(doc)
        if not blocks:
            raise RuleNotApplicable(self.code, detail="未找到“一般公共预算财政拨款支出决算具体情况”段落")
        joined = ""
        spans: List[Tuple[int, int, int]] = []  # (joined_start, block_index, block_offset)
        for index, (_, _, flat, _, begin, end) in enumerate(blocks):
            spans.append((len(joined), index, begin))
            joined += flat[begin:end]

        def _locate(pos: int) -> Tuple[int, str, int, int]:
            for start, index, begin in reversed(spans):
                if pos >= start:
                    page, text, flat, positions, *_ = blocks[index]
                    s, e = flat_span(positions, begin + pos - start, begin + pos - start + 16)
                    return page, text, s, e
            page, text, flat, positions, begin, _ = blocks[0]
            s, e = flat_span(positions, begin, begin + 16)
            return page, text, s, e

        issues: List[Issue] = []
        unresolved: List[str] = []
        unresolved_codes: List[str] = []
        total = _DETAIL_TOTAL_RE.search(joined)
        starts = [m.start() for m in _ITEM_START_RE.finditer(joined)]
        # 总额必须在逐项条目之前；否则命中的只是某项自己的“年初预算…支出决算…”，
        # 不能拿它当该部分总额（否则会把第一项误当总额并重复计缺失）。
        if total is not None and starts and total.start() >= starts[0]:
            total = None
        items: List[Dict[str, Any]] = []
        for index, start in enumerate(starts):
            stop = starts[index + 1] if index + 1 < len(starts) else len(joined)
            head = re.split(r"主要用于|。", joined[start:stop], maxsplit=1)[0]
            items.append({"pos": start, "stop": stop, "head": head})
        if not items:
            raise RuleNotApplicable(self.code, detail="具体情况段落未识别到逐项列示条目")
        for item in items:
            if re.search(r"[（(]\s*类\s*[)）]", item["head"]) and not re.search(r"[（(]\s*项\s*[)）]", item["head"]):
                page, text, s, e = _locate(item["pos"])
                issues.append(issue_at(
                    self, f"支出决算具体情况未细化到项：“{item['head'][:30]}”只列至类/款级科目。",
                    page, text, s, e, severity="warn", checklist_item="A-09",
                ))
        listed_final = 0
        if total:
            # 一个编号下可能挂多条“类（类）款（款）项（项）”子条目（每条各有年初预算与支出决算），
            # 所以按总额之后出现的全部声明加总，不按编号取第一处。
            # 单位按原文（元/万元/亿元）归一；不能把“元”项按“万元”名义值补入。
            tail_start = total.end()
            records: List[Dict[str, Any]] = []
            for match in _DECL_RE.finditer(joined, tail_start):
                raw, unit = match.group("amount"), match.group("unit")
                value = _dec(raw)
                factor = _AMOUNT_UNIT_FACTOR.get(unit)
                records.append({
                    "kind": _decl_kind(match.group("kind")),
                    "raw": raw, "unit": unit, "pos": match.start(),
                    "value_wan": value * factor if value is not None and factor is not None else None,
                    "not_included_reason": (
                        None if value is not None and factor is not None
                        else "amount_or_unit_unusable"
                    ),
                })
            # 应披露集合来自识别到的项级条目：每条科目都要有年初预算与支出决算
            # 两个槽位。没有数字不是“没有检查义务”，缺失槽位同样进入未纳入清单。
            clauses = _subject_clauses(joined, items)
            missing_slots, conflicts = _clause_declaration_accounting(
                joined, clauses, records, tail_start
            )
            listed_final = sum(1 for record in records if record["kind"] == "final")
            for key, label, kind, checklist in (
                (1, "年初预算", "budget", "C-11"), (2, "支出决算", "final", "C-12"),
            ):
                kind_records = [record for record in records if record["kind"] == kind]
                included = [
                    record for record in kind_records
                    if record["value_wan"] is not None and record["pos"] not in conflicts
                ]
                not_included = {
                    record["pos"]: record
                    for record in kind_records
                    if record["value_wan"] is None or record.get("not_included_reason")
                }
                for record in missing_slots.get(kind, []):
                    not_included[record["pos"]] = record
                not_included_items = [not_included[pos] for pos in sorted(not_included)]
                if not kind_records and not not_included_items:
                    continue
                stated = _dec(total.group(key))
                from .native_table_arithmetic import receipt as relation_receipt
                if stated is None:
                    unresolved.append(f"{label}总额缺失，无法复算分项加总")
                    unresolved_codes.append("stated_total_missing")
                    relation_receipt(
                        self, doc, f"V33-251:{kind}:item_sum", [],
                        status="insufficient_data",
                        formula="sum(item declared amounts normalized by original unit) == stated section total (万元)",
                        reason=f"{label}总额缺失，无法复算分项加总",
                        reason_codes=["stated_total_missing"],
                    )
                    continue
                gap_summary = _completeness_summary(not_included_items, label)
                gap_codes = _completeness_codes(not_included_items)
                if not included:
                    detail = (
                        f"{label}无已纳入分项，{len(not_included_items)}处金额未纳入合计"
                        f"（{gap_summary}），不能发布分项加总"
                    )
                    unresolved.append(detail)
                    unresolved_codes.extend(gap_codes or ["item_amount_not_included"])
                    relation_receipt(
                        self, doc, f"V33-251:{kind}:item_sum", [],
                        status="insufficient_data",
                        formula="sum(item declared amounts normalized by original unit) == stated section total (万元)",
                        reason=detail,
                        reason_codes=gap_codes or ["item_amount_not_included"],
                    )
                    continue
                subtotal = sum((record["value_wan"] for record in included), Decimal(0))
                envelope = display_envelope(
                    [total.group(key), *[format(record["value_wan"], "f") for record in included]]
                )
                difference = subtotal - stated
                mismatch = abs(difference) > envelope
                if not mismatch and not not_included_items:
                    relation_receipt(
                        self, doc, f"V33-251:{kind}:item_sum", [],
                        status="pass",
                        formula="sum(item declared amounts normalized by original unit) == stated section total (万元)",
                        reason=None,
                        reason_codes=[],
                    )
                    continue
                unit_items = [record for record in included if record["unit"] != "万元"]
                anchors = []
                for record in unit_items:
                    page, text, s, e = _locate(record["pos"])
                    anchors.append({
                        "page": page, "amount": record["raw"], "unit": record["unit"],
                        "value_wan": format(record["value_wan"], "f"),
                        "section": "支出决算具体情况", "span": text[s:e],
                    })
                page, text, s, e = _locate(total.start())
                if mismatch:
                    parts = [
                        f"支出决算具体情况逐项列示的{label}合计{format(subtotal, 'f')}万元"
                        f"（纳入{len(included)}项），与该部分所列{label}总额{format(stated, 'f')}万元不一致，"
                        f"相差{format(abs(difference), 'f')}万元"
                    ]
                else:
                    parts = [
                        f"支出决算具体情况逐项列示的{label}合计{format(subtotal, 'f')}万元"
                        f"（纳入{len(included)}项），与总额{format(stated, 'f')}万元在允许容差内一致"
                    ]
                if not_included_items:
                    parts.append(
                        f"另有{gap_summary}，未纳入合计，不能视为完整分项加总"
                    )
                conditional_assumed = conditional_included = conditional_delta = None
                if unit_items:
                    units = "、".join(sorted({record["unit"] for record in unit_items}))
                    conditional_assumed = sum(
                        (_dec(record["raw"]) or Decimal(0) for record in unit_items), Decimal(0)
                    )
                    conditional_included = sum(
                        (record["value_wan"] for record in unit_items), Decimal(0)
                    )
                    conditional_delta = conditional_assumed - conditional_included
                    parts.append(f"其中{len(unit_items)}项原文以{units}列示，已按原文单位折算为万元")
                    basis = (
                        f"当前已纳入{format(conditional_included, 'f')}万元，"
                        f"若按万元名义值替换为{format(conditional_assumed, 'f')}万元"
                    )
                    if conditional_delta > 0:
                        parts.append(
                            f"条件性说明：若这些项按万元名义值计入，合计将增加"
                            f"{format(conditional_delta, 'f')}万元（{basis}）；"
                            "本规则不采用该假设，单位问题另行确认"
                        )
                    elif conditional_delta < 0:
                        parts.append(
                            f"条件性说明：若这些项按万元名义值计入，合计将减少"
                            f"{format(abs(conditional_delta), 'f')}万元（{basis}）；"
                            "本规则不采用该假设，单位问题另行确认"
                        )
                    else:
                        parts.append(
                            f"条件性说明：若这些项按万元名义值计入，合计不变（{basis}）；"
                            "本规则不采用该假设，单位问题另行确认"
                        )
                if not not_included_items and subtotal < stated:
                    parts.append("可能漏列科目")
                elif not not_included_items and subtotal > stated:
                    parts.append("分项合计超出总额")
                message = "；".join(parts) + "。"
                issues.append(issue_at(
                    self, message, page, text, s, e, severity="warn", checklist_item=checklist,
                    included_items=[
                        {**{k: v for k, v in record.items() if k != "value_wan"},
                         "value_wan": format(record["value_wan"], "f")}
                        for record in included
                    ],
                    not_included_items=not_included_items,
                    unit_normalized_items=anchors,
                    stated_total_wan=format(stated, "f"),
                    included_subtotal_wan=format(subtotal, "f"),
                    difference_wan=format(difference, "f"),
                    completeness="partial" if not_included_items else "complete",
                    **({"conditional_assumed_wan": format(conditional_assumed, "f"),
                        "conditional_included_wan": format(conditional_included, "f"),
                        "conditional_delta_wan": format(conditional_delta, "f")}
                       if conditional_delta is not None else {}),
                ))
                receipt_status = (
                    "insufficient_data" if not_included_items
                    else "fail" if mismatch else "pass"
                )
                receipt_codes: List[str] = []
                if not_included_items:
                    receipt_codes.extend(gap_codes or ["item_amount_not_included"])
                if mismatch:
                    receipt_codes.append("item_sum_mismatch")
                if unit_items:
                    receipt_codes.append("source_unit_normalized")
                relation_receipt(
                    self, doc, f"V33-251:{kind}:item_sum", [],
                    status=receipt_status,
                    formula="sum(item declared amounts normalized by original unit) == stated section total (万元)",
                    reason=None if receipt_status == "pass" else message,
                    reason_codes=receipt_codes,
                )
                if not_included_items:
                    unresolved.append(
                        f"{label}有{len(not_included_items)}处金额未纳入合计：{gap_summary}"
                    )
                    unresolved_codes.extend(gap_codes or ["item_amount_not_included"])
        if total is None and items:
            unresolved.append("具体情况段落未识别到年初预算/支出决算总额，分项加总无从复算")
            unresolved_codes.append("stated_total_missing")
            from .native_table_arithmetic import receipt as relation_receipt
            relation_receipt(
                self, doc, "V33-251:total:missing", [],
                status="insufficient_data",
                formula="sum(item declared amounts normalized by original unit) == stated section total (万元)",
                reason="具体情况段落未识别到年初预算/支出决算总额，分项加总无从复算",
                reason_codes=["stated_total_missing"],
            )
        issues.extend(self._coverage(doc, joined, items, _locate, listed_final))
        if unresolved:
            raise RuleDeferred(
                self.code, detail="；".join(unresolved), partial_issues=issues,
                unresolved_reasons=list(dict.fromkeys(unresolved)),
                reason_codes=list(dict.fromkeys(unresolved_codes)),
            )
        return issues

    def _coverage(self, doc: Document, joined: str, items, locate,
                  listed_final: int = 0) -> List[Issue]:
        """决算表项级（7 位）本年支出非零的科目，应在具体情况中逐项出现。"""
        facts, reason = table_facts(doc, "FIN_05_general_public_expenditure")
        if reason:
            return []
        # 项级以编码层级判定：2022 版补零编码里 2080000/2080500 是类/款，不是项。
        from src.services.fiscal_code_hierarchy import leaf_codes

        try:
            leaf_set = leaf_codes({str(f.subject_code) for f in facts if str(f.subject_code or "").isdigit()})
        except ValueError:
            return []
        leaves = {}
        for fact in facts:
            code = str(fact.subject_code or "")
            if (len(code) == 7 and code in leaf_set and fact.indicator == "total_actual"
                    and _canonical_budget_stage(fact.budget_stage) == "决算"
                    and fact.normalized_value not in (None, Decimal(0))):
                leaves[code] = re.sub(r"\s+", "", fact.subject_name or "")
        missing = [name for name in leaves.values() if name and name not in joined]
        listed = max(len(items), listed_final)
        if not missing or listed >= len(leaves):
            return []
        page, text, s, e = locate(0)
        return [issue_at(
            self, f"支出决算具体情况漏列科目：决算表项级科目{len(leaves)}个，说明只列{listed}项，"
            f"未见“{'、'.join(missing[:4])}”{'等' if len(missing) > 4 else ''}。",
            page, text, s, e, severity="warn", checklist_item="A-09", missing_subjects=missing[:10],
        )]


# ---------------------------------------------------------------------------
# V33-252 财政拨款收支总表基金/国资列 ↔ 对应决算表
# ---------------------------------------------------------------------------


def _total_fact(facts: Sequence[Any], indicator: str, names: Sequence[str] = ()) -> Optional[Any]:
    candidates = [f for f in facts if f.indicator == indicator and f.subject_code == "total"
                  and _canonical_budget_stage(f.budget_stage) == "决算"
                  and (not names or any(n in re.sub(r"\s+", "", f.subject_name or "") for n in names))]
    return candidates[0] if len(candidates) == 1 else None


class R33252_FundColumnsCrossTable(Rule):
    code, severity = "V33-252", "error"
    desc = "财政拨款收支总表政府性基金/国有资本经营列与对应决算表一致（清单 C-04）"

    def apply(self, doc: Document) -> List[Issue]:
        t4, reason = table_facts(doc, "FIN_04_fiscal_grant_total")
        if reason:
            raise RuleDeferred(self.code, detail=reason, unresolved_reasons=[reason])
        issues: List[Issue] = []
        unresolved: List[str] = []
        for indicator, table_code, title in (
            ("government_fund_budget", "FIN_08_gov_fund", "政府性基金预算财政拨款收入支出决算表"),
            ("state_capital_budget", "FIN_09_state_capital", "国有资本经营预算财政拨款收入支出决算表"),
        ):
            column = _total_fact(t4, indicator, ("本年支出合计",))
            if column is None or column.normalized_value is None:
                continue
            column_value = Decimal(str(column.normalized_value))
            detail, detail_reason = table_facts(doc, table_code)
            if detail_reason:
                if column_value != 0:
                    unresolved.append(f"{title}：{detail_reason}")
                continue
            target = _total_fact(detail, "total_actual")
            if target is None:
                classes = [f for f in detail if f.indicator == "total_actual" and len(str(f.subject_code or "")) == 3
                           and _canonical_budget_stage(f.budget_stage) == "决算" and f.normalized_value is not None]
                target_value = sum((Decimal(str(f.normalized_value)) for f in classes), Decimal(0)) if classes else None
            else:
                target_value = Decimal(str(target.normalized_value)) if target.normalized_value is not None else None
            if target_value is None:
                if column_value != 0:
                    unresolved.append(f"{title}本年支出合计未取到")
                continue
            level, diff = classify_amount_diff(column_value, target_value, 1)
            if level != "mismatch":
                continue
            refs = list(column.extra_dims.get("evidence_refs", []) or [])
            page = refs[0].get("physical_page") if refs else None
            text = str(doc.page_texts[page - 1] if page and page <= len(doc.page_texts) else "")
            pos = text.find("本年支出合计")
            if not page or pos < 0:
                unresolved.append(f"财政拨款收支总表“{title[:6]}”列本年支出合计原文未定位")
                continue
            issues.append(issue_at(
                self, f"财政拨款收入支出决算总表“{title[:7]}”列本年支出合计{column_value:.2f}万元，与《{title}》"
                f"本年支出合计{target_value:.2f}万元不一致，相差{diff:.2f}万元。",
                page, text, pos, pos + 6, severity="error", checklist_item="C-04", related_table=table_code,
            ))
        if unresolved:
            raise RuleDeferred(self.code, detail="；".join(unresolved), partial_issues=issues,
                               unresolved_reasons=unresolved)
        return issues


# ---------------------------------------------------------------------------
# CMM-026 有数据的表不得保留空表说明
# ---------------------------------------------------------------------------

_EMPTY_NOTE_RE = re.compile(r"(?:故|因此|所以)?本表(?:无数据|为空表|无内容)|无此项(?:资金|预算)?(?:安排)?[，,]?故本表")
_SUBJECTS = ("政府性基金", "国有资本经营", "三公")


def _has_data(table: Sequence[Sequence[Any]]) -> bool:
    for row in table or []:
        for cell in row or []:
            text = re.sub(r"[,\s]", "", str(cell or ""))
            if re.fullmatch(r"-?\d+\.\d+", text) and Decimal(text) != 0:
                return True
    return False


class CMM026_EmptyNoteOnDataTable(Rule):
    code, severity = "CMM-026", "warn"
    desc = "有数据的表不得保留空表说明（预/决算通用，清单 A-07）"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        tables = list(getattr(doc, "page_tables", None) or [])
        for page, text in iter_pages(doc):
            if not page_has_table(doc, page - 1):
                continue
            note = _EMPTY_NOTE_RE.search(text)
            if not note:
                continue
            page_tables = tables[page - 1] if page - 1 < len(tables) else []
            if not any(_has_data(t) for t in page_tables):
                continue
            title = compact("\n".join(text.split("\n")[:4]))
            sentence_start = max(text.rfind("\n", 0, note.start()), text.rfind("注", 0, note.start()))
            sentence = compact(text[max(0, sentence_start):note.end()])
            subject = next((s for s in _SUBJECTS if s in sentence), None)
            if subject and subject not in title:
                continue  # 说明对象与本页数据表不是同一张表（同页另有空表）
            issues.append(issue_at(
                self, "表格已有数据，但表下仍保留“本表无数据/空表”说明，应删除该说明。",
                page, text, max(0, note.start() - 20), note.end(), severity="warn", checklist_item="A-07",
            ))
        return issues


CHECKLIST_FINAL_RULES: List[Rule] = [
    R33251_FunctionalDetailCompleteness(),
    R33252_FundColumnsCrossTable(),
]

def _register() -> None:
    from . import rules_v33

    known = {getattr(rule, "code", None) for rule in rules_v33.ALL_RULES}
    rules_v33.ALL_RULES.extend(rule for rule in CHECKLIST_FINAL_RULES if rule.code not in known)


_register()

__all__ = [
    "CHECKLIST_FINAL_RULES",
    "CMM026_EmptyNoteOnDataTable",
    "R33251_FunctionalDetailCompleteness",
    "R33252_FundColumnsCrossTable",
]
