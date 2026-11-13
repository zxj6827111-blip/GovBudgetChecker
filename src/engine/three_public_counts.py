"""三公经费数量要素披露（2026-10-22 口径决议④；清单决算 A-08、预算 A-09）。

金额驱动：三公表（FIN_07）对应项金额大于 0 时，三公说明须披露数量——
公务用车购置费→购置数量；因公出国（境）费→团组数、人数；公务接待费→
批次、人数，缺则 medium。金额为 0、或说明写明未发生/未安排，视为已披露；
表内金额取不到或不唯一时不要求数量（宁漏报不误报）。车辆保有量是存量
指标，始终要求，全文（含国有资产占有使用情况）任一处披露即可。

与 V33-246 的分工：V33-246 处理“已写批次/人次但只写了外宾”的不完整
披露；本模块只在说明里完全没有对应数量时报出，两者不重叠。
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .fact_context import _canonical_budget_stage, operand_rejection_reason

THREE_PUBLIC_TABLE = "FIN_07_three_public"

_NUM = r"(?:\d+|[一二三四五六七八九十两]+)"
#: 说明里的车辆称谓：除“公务用车”外，实样还会写特种专业技术用车/专业技术用车/其他用车。
_VEHICLE_NOUN = r"(?:公务用车|公务车辆|公务车|特种专业技术用车|专业技术用车|其他用车|车辆)"

#: (三公表指标, 标签, 说明内主体词干, ((数量字段, 字段名, 披露形态), ...))
_COUNT_SPECS: Tuple[Tuple[str, str, str, Tuple[Tuple[str, str, "re.Pattern[str]"], ...]], ...] = (
    (
        "vehicle_purchase",
        "公务用车购置费",
        r"公务用车购置(?!及)|购置公务用车|购置车辆",
        (
            (
                "purchase_count",
                "公务用车购置数量",
                re.compile(
                    rf"(?:购置|购买|新购|更新)了?{_VEHICLE_NOUN}?{_NUM}辆"
                    rf"|购置(?:数量?|车辆数)(?:为|共|计)?{_NUM}辆?"
                    rf"|{_NUM}辆{_VEHICLE_NOUN}?的?(?:购置|更新)"
                ),
            ),
        ),
    ),
    (
        "overseas",
        "因公出国（境）费",
        r"因公出国|出访",
        (
            (
                "overseas_groups",
                "因公出国（境）团组数",
                re.compile(rf"{_NUM}个?(?:出访|出国(?:[（(]境[）)])?)?团组|团组(?:数)?(?:为|共|计)?{_NUM}个?"),
            ),
            (
                "overseas_people",
                "因公出国（境）人数",
                re.compile(rf"(?:团组|出访|出国)[^。；;]{{0,30}}?{_NUM}人(?:次)?|{_NUM}人(?:次)?[^。；;]{{0,8}}(?:出访|出国)"),
            ),
        ),
    ),
    (
        "reception",
        "公务接待费",
        r"公务接待|接待",
        (
            ("reception_batches", "公务接待批次", re.compile(rf"{_NUM}个?批次?|批次(?:为|共|计)?{_NUM}")),
            (
                "reception_people",
                "公务接待人数",
                re.compile(rf"批次?[^。；;]{{0,12}}?{_NUM}人(?:次)?|接待[^。；;]{{0,30}}?{_NUM}人(?:次)?|人次(?:为|共|计)?{_NUM}"),
            ),
        ),
    ),
)

_NOT_OCCURRED_RE = re.compile(
    r"未(?:发生|安排|组织|开展|新增|购置|新购|更新)(?!变化|改变|变动)|无(?:此项|相关)?(?:支出|安排)"
)
_VEHICLE_STOCK_RE = re.compile(
    r"保有量?|(?:共有|实有|现有|拥有)(?:公务用车|公务车辆|公车|车辆)|车辆(?:总数|总量)"
)
#: pdfplumber 对无 ToUnicode 的字形输出 ``(cid:1)`` 占位，位置多在数字与量词之间。
#: 它不代表披露内容，数量要素匹配前必须剔除，否则“团组(cid:1)2 个”会被当成未披露。
_CID_TOKEN_RE = re.compile(r"\(cid:\d+\)", re.IGNORECASE)

#: 三公表规范表名（与 NINE_TABLES 一致）。空表说明必须与表格同页或相邻
#: 续页才认——防止把其他表的"本表无数据"当成三公表的依据。
_FIN_07_TITLE = "财政拨款“三公”经费支出决算表"

#: 三公表空表说明（Codex 返工第 8 项："表格合法空白/空表说明"要能得到
#: 合法适用性结论）。说明写"本表无数据"而表内另有非零金额事实时仍按取数
#: 不足处理——说明不能掩盖真实数据（见 ``_legitimate_zero_basis`` 的守卫）。
_EMPTY_TABLE_NOTE_RE = re.compile(
    r"(?:没有|无|未发生|未安排|不存在)[^。；;]{0,40}(?:财政拨款)?[“\"]?三公[”\"]?"
    r"经费[^。；;]{0,24}支出[^。；;]{0,40}(?:故|因此)?本表(?:无数据|为空表|无内容)"
    r"|本表(?:无数据|为空表|无内容)[^。；;]{0,40}(?:没有|无|未发生)[^。；;]{0,24}三公"
)


def _compact_text(value: str) -> str:
    """去空白与未映射字形占位，保留其余原文（不重写数字与名称）。"""
    return re.sub(r"\s+", "", _CID_TOKEN_RE.sub("", value or ""))



def _stage_matches(fact: Any, report_kind: str) -> bool:
    stage = _canonical_budget_stage(getattr(fact, "budget_stage", None))
    return stage == "决算" if report_kind == "final" else stage in ("年初预算", "全年预算")


def item_amounts(doc: Any, report_kind: str) -> dict:
    """三公表各项金额（万元）：每项须唯一命中当前材料、当前阶段的合计行事实。

    D0 RC-5a：取不到数时通过 ``last_amount_gaps``（随 doc 记账）显式留痕，
    不再静默返回空表——"没有检查"必须可被覆盖统计看到（2026-10-22 决议
    只授权"不报问题"，未授权"不记账"）。
    """
    context = getattr(doc, "fact_context", None)
    gaps: List[Dict[str, str]] = []
    try:
        doc._quantity_amount_gaps = gaps
    except Exception:
        pass
    if context is None:
        gaps.append({"reason": "fact_context_missing", "detail": "无统一财政事实上下文"})
        return {}
    org_name = getattr(context, "org_name", None)
    facts = [f for f in getattr(context, "facts", []) or [] if f.table_code == THREE_PUBLIC_TABLE]
    instances = {f.instance_uid for f in facts}
    if len(instances) != 1:
        gaps.append({
            "reason": "table_instance_not_unique",
            "detail": f"三公表逻辑实例数={len(instances)}（需唯一）",
        })
        return {}
    amounts = {}
    for indicator, *_rest in _COUNT_SPECS:
        candidates = [
            f for f in facts
            if f.indicator == indicator and f.subject_code == "total"
            and f.normalized_value is not None and _stage_matches(f, report_kind)
        ]
        if len(candidates) == 1 and not operand_rejection_reason(candidates[0], org_name):
            amounts[indicator] = Decimal(str(candidates[0].normalized_value))
        else:
            gaps.append({
                "reason": "amount_fact_missing_or_ambiguous",
                "detail": (
                    f"{indicator} 候选 {len(candidates)} 条"
                    + ("（操作数被拒绝）" if len(candidates) == 1 else "（需唯一）")
                ),
            })
    return amounts


def _item_paragraphs(paras: Sequence[str], stem: str) -> List[str]:
    """说明内属于该项的段落：段首编号切换主体，段内出现词干也算。"""
    picked: List[str] = []
    current = False
    for para in paras:
        head = re.match(r"\s*[（(]?[一二三四五六七八九十\d]+[）)、.．]\s*(\S{0,12})", para)
        if head:
            current = bool(re.search(stem, head.group(1) or ""))
        if current or re.search(stem, para):
            picked.append(para)
    return picked


def _three_public_has_nonzero_fact(doc: Any) -> bool:
    """三公表是否已有非零金额事实（空表说明只对真空表成立）。"""
    context = getattr(doc, "fact_context", None)
    if context is None:
        return False
    for fact in getattr(context, "facts", []) or []:
        if getattr(fact, "table_code", None) != THREE_PUBLIC_TABLE:
            continue
        value = getattr(fact, "normalized_value", None)
        if value is None:
            continue
        try:
            if Decimal(str(value)) != 0:
                return True
        except Exception:
            return True
    return False


def _page_of_text(doc: Any, text: str) -> Optional[int]:
    """在原件页文本层里定位一句原文所在页（归一后前缀命中）。"""
    needle = _compact_text(text)
    if len(needle) < 8:
        return None
    for index, page_text in enumerate(getattr(doc, "page_texts", None) or [], 1):
        if needle[:60] in _compact_text(page_text):
            return index
    return None


#: 说明章节内的"本年度无三公经费支出"类断言。只在三公说明章节范围内使用，
#: 并在句中排除否定/条件/引用语境（fail-closed：拿不准就不授权零）。
_TOTAL_NONE_RE = re.compile(
    r"(?:无|没有|未发生|不存在|未安排|未开展)[^。；;]{0,30}三公[^。；;]{0,24}支出")
_NONE_STATEMENT_GUARD_RE = re.compile(
    r"并非|不是|不成立|不属实|无证据|不得|不能|不应|如果|假设|假如|倘若|如若|若(?!干)"
    r"|示例|样例|模板|仅供参考|假定|虚构|演示|[？?]")


def _scope_none_statement(scope: str, doc: Any) -> Optional[Tuple[str, List[dict]]]:
    """三公说明章节里明确的"无三公经费支出"断言 → (依据文本, 原文出处)。

    只认章节内断言句：句内出现否定/条件/引用/疑问语境时不授权零。
    表内已有非零金额事实时，"无三公支出"说明与该事实矛盾，不能拿它
    授权零——保留取数不足，让矛盾由金额规则报告。
    """
    if _three_public_has_nonzero_fact(doc):
        return None
    compact = _compact_text(scope)
    if not compact:
        return None
    for match in _TOTAL_NONE_RE.finditer(compact):
        context_begin = max(
            compact.rfind("。", 0, match.start()),
            compact.rfind("；", 0, match.start()),
            compact.rfind(";", 0, match.start()),
        ) + 1
        context_end = match.end()
        for candidate in ("。", "；", ";"):
            position = compact.find(candidate, match.end())
            if position != -1:
                context_end = max(context_end, position + 1)
        sentence_context = compact[context_begin:context_end]
        if _NONE_STATEMENT_GUARD_RE.search(sentence_context):
            continue
        page = _page_of_text(doc, sentence_context)
        basis = "说明明确无三公经费支出（" + (
            f"第{page}页" if page else "页码未能定位") + "）"
        refs: List[dict] = []
        if page:
            refs.append({"page": page, "text": sentence_context[:80],
                         "note": "no_expenditure_statement"})
        return basis, refs
    return None


def _empty_table_basis(doc: Any) -> Optional[Tuple[str, List[dict]]]:
    """三公表"本表无数据"空表说明 → 合法适用性依据（仅真空表）。"""
    pages = [str(text or "") for text in (getattr(doc, "page_texts", None) or [])]
    if not pages:
        return None
    title_pages = [index for index, text in enumerate(pages, 1)
                   if _FIN_07_TITLE in _compact_text(text)]
    if not title_pages:
        return None
    for index, text in enumerate(pages, 1):
        compact = _compact_text(text)
        if not _EMPTY_TABLE_NOTE_RE.search(compact):
            continue
        if not any(abs(index - title_page) <= 1 for title_page in title_pages):
            continue
        if _three_public_has_nonzero_fact(doc):
            # 说明写"本表无数据"但表内已有非零事实：说明不能掩盖数据。
            return None
        return (
            f"三公表下空表说明（第{index}页：本表无数据）",
            [{"page": index, "table_code": THREE_PUBLIC_TABLE,
              "note": "empty_table_statement"}],
        )
    return None


def _legitimate_zero_basis(
    doc: Any,
    indicator: str,
    report_kind: str,
    scope: str,
) -> Optional[Tuple[str, List[dict]]]:
    """金额事实取不到时的合法零依据（仅用于"不要求数量披露"的适用性判断）。

    依据只有四类，全部来自原件原文/原格且带页码：
    1. 说明里本项决算数明确为 0（或三公合计决算为 0）；
    2. 三公表本项决算格原件空白且经空白值约定（U-5）认证为 0；
    3. 三公表下空表说明（与表同页/相邻续页，且表内无非零事实）；
    4. 三公说明章节明确"无三公经费支出"。
    四类都拿不到时保持取数不足（不猜零）。
    """
    if report_kind == "final":
        for key in (indicator, "total_actual"):
            try:
                from .narrative_zero_evidence import narrative_zero_sources

                refs = narrative_zero_sources(
                    doc, table_code=THREE_PUBLIC_TABLE, indicator=key, stage="决算")
            except Exception:  # noqa: BLE001 - 零证据不可用时不授权零
                refs = None
            if refs:
                label = "本项决算为0" if key != "total_actual" else "三公经费决算合计为0"
                page = refs[0].get("page") if isinstance(refs[0], dict) else None
                basis = f"说明明确{label}（" + (
                    f"第{page}页" if page else "页码未定位") + "）"
                return basis, [dict(ref) for ref in refs if isinstance(ref, dict)]
        # 原格空白：回到原件网格认证的空白金额格 + 空白值约定（U-5）。
        try:
            from .native_empty_operands import blank_term, independent_blank_zero

            term = blank_term(doc, THREE_PUBLIC_TABLE, indicator,
                              code="total", stage="决算")
        except Exception:  # noqa: BLE001 - 认证不可用时不授权零
            term = None
        if term is not None and independent_blank_zero(doc, term):
            observation = (term.extra_dims or {}).get("native_blank_observation") or {}
            page = observation.get("physical_page")
            basis = "三公表本项决算格原件空白并经空白值约定（U-5）认证为0（" + (
                f"第{page}页" if page else "页码未定位") + "）"
            refs = [{
                "page": page,
                "table_code": THREE_PUBLIC_TABLE,
                "indicator": indicator,
                "source_cell_id": observation.get("source_cell_id"),
                "bbox": observation.get("bbox"),
                "note": "certified_blank_zero",
            }]
            return basis, refs
    empty = _empty_table_basis(doc)
    if empty is not None:
        return empty
    statement = _scope_none_statement(scope, doc)
    if statement is not None:
        return statement
    return None


def count_issues(
    rule: Any,
    doc: Any,
    *,
    report_kind: str,
    scope: str,
    section_title: Optional[str],
    locate_page,
    items: Optional[Sequence[str]] = None,
) -> List[Any]:
    """返回数量要素缺失的问题列表（每个缺失字段一条，medium）。

    ``items`` 限定参与的三公项（预算侧按清单 A-09 只要求购置数量）。
    """
    from src.utils.narration import merge_page_texts, merge_soft_wrapped_lines

    paras = merge_soft_wrapped_lines(scope)
    amounts = item_amounts(doc, report_kind)
    stage_label = "决算数" if report_kind == "final" else "预算数"
    issues: List[Any] = []

    # D0 RC-5a：数量要素逐项显式记账（关系级回执），消灭"零问题零回执"的
    # 静默跳过——2026-10-22 决议"宁漏报不误报"只约束不报问题，不授权不记账。
    def _receipt(sub_check: str, status: str, *, formula: str,
                 incomplete_reason: Optional[str] = None,
                 disclosure_sources: Sequence[dict] = ()) -> None:
        from .rules_v33 import _v33_relation_receipt

        _v33_relation_receipt(
            doc, str(getattr(rule, "code", "") or "QUANTITY"), sub_check,
            status=status, formula=formula, incomplete_reason=incomplete_reason,
            disclosure_sources=disclosure_sources,
            evidence_nature="applicability_judgment",
        )

    quantity_gaps = {
        gap.get("detail"): gap for gap in (getattr(doc, "_quantity_amount_gaps", None) or [])
    }
    for indicator, label, stem, fields in _COUNT_SPECS:
        if items is not None and indicator not in items:
            continue
        amount = amounts.get(indicator)
        sub_check = f"quantity:{indicator}"
        if amount is None:
            # Codex 返工第 8 项：金额事实取不到时先找**合法适用性依据**
            # （说明明确为 0 / 空表说明 / 明确无三公支出）。有依据 → 有据
            # 不适用（带页码出处）；无依据 → 保持取数不足。
            basis = _legitimate_zero_basis(doc, indicator, report_kind, scope)
            if basis is not None:
                basis_text, basis_refs = basis
                _receipt(sub_check, "not_applicable",
                         formula=f"{label}{stage_label}取数不足，但{basis_text}，"
                                 "金额驱动数量义务不适用",
                         disclosure_sources=list(basis_refs))
                continue
            gap_values = list(quantity_gaps.values())
            gap = next(
                (g for g in gap_values if indicator in str(g.get("detail", ""))),
                gap_values[0] if len(gap_values) == 1 else None,
            )
            _receipt(sub_check, "insufficient_data",
                     formula=f"{label}{stage_label} > 0 ⇒ require count disclosure",
                     incomplete_reason=(gap or {}).get("detail") or "三公表该项金额事实取数不足")
            continue
        if amount <= 0:
            _receipt(sub_check, "not_applicable",
                     formula=f"{label}{stage_label}={amount}，金额为0不要求数量披露")
            continue
        item_paras = _item_paragraphs(paras, stem)
        compact = _compact_text("".join(item_paras))
        if _NOT_OCCURRED_RE.search(compact):
            _receipt(sub_check, "not_applicable",
                     formula=f"{label}{stage_label}={amount}，说明写明未发生")
            continue
        missing_fields = [field_label for field_key, field_label, pattern in fields
                          if not pattern.search(compact)]
        if missing_fields:
            _receipt(sub_check, "fail",
                     formula=f"{label}{stage_label}={amount} > 0 且说明未披露 {'、'.join(missing_fields)}")
        else:
            _receipt(sub_check, "pass",
                     formula=f"{label}{stage_label}={amount} > 0 且数量要素已披露")
        evidence = item_paras[0] if item_paras else (paras[0] if paras else scope[:200])
        for field_key, field_label, pattern in fields:
            if pattern.search(compact):
                continue
            issues.append(rule._issue(
                f"三公经费数量要素未披露：{label}{stage_label}{amount:.2f}万元，"
                f"但三公经费说明未披露{field_label}。请补充{field_label}"
                "（金额为 0 或未发生的无需披露数量）。",
                {
                    "page": locate_page(doc, evidence[:40]),
                    "three_public_item": label,
                    "count_field": field_key,
                    "item_amount_wan": str(amount),
                    "section": (section_title or "").strip()[:60] or None,
                    "expected_disclosure": f"披露{field_label}",
                },
                severity="medium",
                evidence_text=evidence[:200],
            ))

    whole = _compact_text(merge_page_texts(list(getattr(doc, "page_texts", None) or [])))
    _receipt("quantity:vehicle_stock", "fail" if not _VEHICLE_STOCK_RE.search(whole) else "pass",
             formula="全文（含国有资产占有使用情况）任一处披露车辆保有量")
    if not _VEHICLE_STOCK_RE.search(whole):
        evidence = next((p for p in paras if "公务用车" in p), paras[0] if paras else scope[:200])
        issues.append(rule._issue(
            "三公经费数量要素未披露：全文未披露公务用车（车辆）保有量。"
            "请在三公经费说明或国有资产占有使用情况中写明车辆保有量（无车辆的写明 0 辆）。",
            {
                "page": locate_page(doc, evidence[:40]),
                "three_public_item": "公务用车",
                "count_field": "vehicle_stock",
                "section": (section_title or "").strip()[:60] or None,
                "expected_disclosure": "披露车辆保有量",
            },
            severity="medium",
            evidence_text=evidence[:200],
        ))
    return issues
