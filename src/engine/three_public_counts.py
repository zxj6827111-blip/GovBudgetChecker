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
from typing import Any, List, Optional, Sequence, Tuple

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


def _compact_text(value: str) -> str:
    """去空白与未映射字形占位，保留其余原文（不重写数字与名称）。"""
    return re.sub(r"\s+", "", _CID_TOKEN_RE.sub("", value or ""))



def _stage_matches(fact: Any, report_kind: str) -> bool:
    stage = _canonical_budget_stage(getattr(fact, "budget_stage", None))
    return stage == "决算" if report_kind == "final" else stage in ("年初预算", "全年预算")


def item_amounts(doc: Any, report_kind: str) -> dict:
    """三公表各项金额（万元）：每项须唯一命中当前材料、当前阶段的合计行事实。"""
    context = getattr(doc, "fact_context", None)
    if context is None:
        return {}
    artifact = getattr(doc, 'parse_artifact', None)
    if artifact is not None and (context.file_hash != artifact.source_file_hash
            or context.parse_run_uid != artifact.parse_run_uid):
        return {}
    org_name = getattr(context, "org_name", None)
    facts = [f for f in getattr(context, "facts", []) or [] if f.table_code == THREE_PUBLIC_TABLE]
    if len({f.instance_uid for f in facts}) != 1:
        return {}
    amounts = {}
    for indicator, *_rest in _COUNT_SPECS:
        candidates = [
            f for f in facts
            if f.indicator == indicator and f.subject_code == "total"
            and f.normalized_value is not None and _stage_matches(f, report_kind)
            and context.fiscal_year is not None and f.fiscal_year == context.fiscal_year
        ]
        if len(candidates) == 1 and not operand_rejection_reason(candidates[0], org_name):
            amounts[indicator] = Decimal(str(candidates[0].normalized_value))
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


def source_refs(doc, paragraph, role):
    """引用原文，不使用定位器的默认第一页兜底。"""
    import unicodedata
    from .native_narrative_checks import _Text
    text = _Text.from_pages(list(getattr(doc, 'page_texts', []) or []))
    key = re.sub(r'\s+', '', unicodedata.normalize('NFKC', paragraph or ''))
    at = text.compact.find(key) if key else -1
    return text.refs(at, at + len(key), role) if at >= 0 else []


def count_issues(rule, doc, *, report_kind, scope, section_title, locate_page, items=None):
    """每个数量字段单独结账；金额未知不等于数量要求不存在。"""
    from src.utils.narration import merge_page_texts, merge_soft_wrapped_lines
    from .native_table_arithmetic import receipt, finish
    from .native_three_public_ratios import operand_ref
    from .diagnostic_fields import record_gap, text_missing_operand

    paras = merge_soft_wrapped_lines(scope)
    amounts = item_amounts(doc, report_kind)
    context = getattr(doc, 'fact_context', None)
    stage_label = '决算数' if report_kind == 'final' else '预算数'
    issues, unresolved = [], []
    for indicator, label, stem, fields in _COUNT_SPECS:
        if items is not None and indicator not in items:
            continue
        amount = amounts.get(indicator)
        item_paras = _item_paragraphs(paras, stem)
        compact = _compact_text(''.join(item_paras))
        evidence = item_paras[0] if item_paras else (paras[0] if paras else scope[:200])
        sources = [ref for para in item_paras or [evidence]
                   for ref in source_refs(doc, para, label + '数量披露原文')]
        facts = [f for f in getattr(context, 'facts', []) or []
                 if f.table_code == THREE_PUBLIC_TABLE and f.indicator == indicator
                 and f.subject_code == 'total' and _stage_matches(f, report_kind)]
        usable = facts if amount is not None and len(facts) == 1 else []
        exempt = (amount is not None and amount <= 0) or bool(_NOT_OCCURRED_RE.search(compact))
        for field_key, field_label, pattern in fields:
            key = 'three_public:count:' + field_key
            present = bool(pattern.search(compact))
            reason = None
            status = 'not_applicable' if exempt else 'pass' if present else 'fail'
            if not present and not exempt and amount is None:
                status = 'insufficient_data'
                reason = f'{label}金额事实缺失、过期或不唯一，无法确定{field_label}披露要求'
                unresolved.append(reason)
                record_gap(doc, reason, reason_codes=['operand_missing'],
                    missing_operands=[text_missing_operand(section=section_title or '三公经费说明',
                        role=field_label + '对应金额', page=sources[0]['page'] if sources else None,
                        row_key=indicator, column=stage_label, stage='决算' if report_kind == 'final' else '年初预算')],
                    preferred_action='system_recheck')
            ids, evidence_ids = receipt(rule, doc, key, usable, status=status,
                formula='positive item amount requires disclosed count; verified zero/no activity is exempt',
                reason=reason, reason_codes=['operand_missing'] if reason else [],
                disclosure_sources=sources, obligation_id='OBL-SG-COUNTS', evidence_nature='disclosure_check')
            if status != 'fail':
                continue
            location = {'page': sources[0]['page'] if sources else None,
                'three_public_item': label, 'count_field': field_key,
                'item_amount_wan': str(amount), 'section': section_title,
                'table_refs': sources, 'operand_refs': [operand_ref(f, '数量触发金额') for f in usable],
                'fact_context_version_token': getattr(context, 'version_token', None)}
            issues.append(rule._issue(
                f'三公经费数量要素未披露：{label}{stage_label}{amount:.2f}万元，'
                f'但三公经费说明未披露{field_label}。请补充{field_label}。',
                location, severity='medium', evidence_text=evidence[:200],
                check_relation_id=f'{rule.code}:{key}', fact_ids=ids, evidence_ids=evidence_ids))

    whole = _compact_text(merge_page_texts(list(getattr(doc, 'page_texts', None) or [])))
    # “保有量”三个字不等于披露数量，必须有数字或明确无车辆的声明。
    stock_pattern = re.compile(
        rf'(?:保有量?|车辆(?:总数|总量))(?:为|共|计|[:：])?{_NUM}辆?'
        rf'|(?:共有|实有|现有|拥有){_VEHICLE_NOUN}(?:为|共|计)?{_NUM}辆'
        rf'|(?:无|没有|未配备)(?:公务用车|公务车辆|车辆)')
    stock_present = bool(stock_pattern.search(whole))
    evidence = next((p for p in paras if '公务用车' in p), paras[0] if paras else scope[:200])
    stock_paras = [p for text in getattr(doc, 'page_texts', []) or []
                   for p in merge_soft_wrapped_lines(text) if stock_pattern.search(_compact_text(p))]
    sources = [ref for para in stock_paras or [evidence]
               for ref in source_refs(doc, para, '车辆保有量披露原文')]
    receipt(rule, doc, 'three_public:count:vehicle_stock', [],
        status='pass' if stock_present else 'fail', formula='vehicle stock explicitly disclosed',
        disclosure_sources=sources, obligation_id='OBL-SG-COUNTS', evidence_nature='disclosure_check')
    if not stock_present:
        issues.append(rule._issue(
            '三公经费数量要素未披露：全文未披露公务用车（车辆）保有量。'
            '请在三公经费说明或国有资产占有使用情况中写明车辆保有量（无车辆的写明0辆）。',
            {'page': sources[0]['page'] if sources else None, 'count_field': 'vehicle_stock',
             'three_public_item': '公务用车', 'section': section_title, 'table_refs': sources},
            severity='medium', evidence_text=evidence[:200],
            check_relation_id=f'{rule.code}:three_public:count:vehicle_stock'))
    return finish(rule, issues, unresolved)
