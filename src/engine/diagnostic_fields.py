"""DH-02 结构化卡点字段：原因码、上游阻断、缺数与候选位置、下一步动作、根因键。

本模块只**增加**结构化诊断，不改变任何检查结论：调用方在原有失败文本产生的
同一处登记结构化事实，文本仍按原样作为兼容 detail 输出。

三条纪律：

1. 结构化字段必须在**产生失败的地方**登记，不能事后按文本猜测“缺失/不唯一”；
2. 位置未知就写 null，不编造页码、单元格编号或科目；
3. 根因键是主阻断点（或已确认的缺口位置）的确定性函数；同点同键、异点异键，
   且与验收工具 ``_root_signature`` 的算法逐字一致，保证回执与义务汇总可对接。
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

#: 冻结原因词表（v1，与验收包 diagnostic_policy.json 的 reasons 键一致）。
FROZEN_REASON_CODES = frozenset(
    {
        "ai_not_requested",
        "ai_partial_citation_rejected",
        "ai_partial_invalid_output",
        "ai_timeout",
        "candidate_amount_conflict",
        "cell_blank_semantics_unknown",
        "cell_low_confidence",
        "cell_unbound",
        "code_name_conflict",
        "column_identity_unconfirmed",
        "column_width_drift",
        "comparison_identity_unresolved",
        "exception_amount_conflict",
        "exception_scope_ambiguous",
        "execution_error",
        "item_amount_not_included",
        "item_disclosure_missing",
        "item_sum_mismatch",
        "name_mismatch_confirmed",
        "operand_ambiguous",
        "operand_missing",
        "parse_error",
        "row_code_missing",
        "row_identity_duplicate",
        "row_level_unresolved",
        "rule_no_reason",
        "scope_unconfirmed",
        "source_unit_normalized",
        "stated_total_missing",
        "subject_conflict",
        "subject_name_unlocated",
        "subject_unconfirmed",
        "sum_scope_not_comparable",
        "table_mapping_incomplete",
        "table_missing",
        "text_binding_failed",
        "text_statement_absent",
        "text_statement_unparsed",
        "withheld_amount_unknown",
    }
)

#: 上游原因 → 允许的阻断维度（与冻结维度表 upstream_reason_dimensions 一致）。
UPSTREAM_REASON_DIMENSIONS: Dict[str, tuple] = {
    "cell_blank_semantics_unknown": ("cell",),
    "cell_low_confidence": ("cell",),
    "cell_unbound": ("cell",),
    "column_identity_unconfirmed": ("column",),
    "column_width_drift": ("column",),
    "row_code_missing": ("row",),
    "row_identity_duplicate": ("row",),
    "row_level_unresolved": ("row",),
    "scope_unconfirmed": ("org_scope", "fund_scope", "budget_stage", "year"),
    "subject_conflict": ("organization",),
    "subject_unconfirmed": ("organization",),
    "table_mapping_incomplete": ("instance_uid",),
    "table_missing": ("instance_uid",),
}

#: 根因层级优先级（与冻结维度表 root_priority 一致）。
ROOT_PRIORITY: Dict[str, int] = {
    "organization": 0,
    "year": 1,
    "fund_scope": 1,
    "budget_stage": 1,
    "org_scope": 1,
    "instance_uid": 2,
    "row": 3,
    "column": 4,
    "cell": 5,
}

#: 结构定位层，任何规则都可使用（与冻结维度表 structural_dimensions 一致）。
STRUCTURAL_DIMENSIONS = ("row", "column", "cell")

#: 冻结策略里的原因优先级与允许动作（与 diagnostic_policy.json v2 逐项一致；
#: 代码无法在运行时读验收包，故按冻结表编译进实现，随包变更须同步修订）。
REASON_POLICY: Dict[str, tuple] = {
    "cell_unbound": (10, ("human_confirm", "system_recheck", "tech_fix")),
    "column_identity_unconfirmed": (10, ("human_confirm", "system_recheck", "tech_fix")),
    "column_width_drift": (10, ("human_confirm", "system_recheck", "tech_fix")),
    "execution_error": (10, ("human_confirm", "system_recheck", "tech_fix")),
    "parse_error": (10, ("human_confirm", "system_recheck", "tech_fix")),
    "row_code_missing": (10, ("human_confirm", "system_recheck", "tech_fix")),
    "row_level_unresolved": (10, ("human_confirm", "system_recheck", "tech_fix")),
    "rule_no_reason": (10, ("human_confirm", "system_recheck", "tech_fix")),
    "table_mapping_incomplete": (10, ("tech_fix",)),
    "text_statement_unparsed": (10, ("system_recheck", "tech_fix")),
    "code_name_conflict": (20, ("human_confirm", "system_recheck", "tech_fix")),
    "name_mismatch_confirmed": (20, ("human_confirm", "system_recheck", "tech_fix")),
    "table_missing": (20, ("human_confirm", "system_recheck", "tech_fix")),
    "text_statement_absent": (20, ("human_confirm", "system_recheck", "tech_fix")),
    "cell_blank_semantics_unknown": (30, ("human_confirm", "system_recheck", "tech_fix")),
    "cell_low_confidence": (30, ("human_confirm", "system_recheck", "tech_fix")),
    "operand_ambiguous": (30, ("human_confirm", "system_recheck", "tech_fix")),
    "operand_missing": (30, ("human_confirm", "system_recheck", "tech_fix")),
    "row_identity_duplicate": (30, ("human_confirm", "system_recheck", "tech_fix")),
    "scope_unconfirmed": (30, ("human_confirm", "system_recheck", "tech_fix")),
    "subject_conflict": (30, ("human_confirm", "system_recheck", "tech_fix")),
    "subject_name_unlocated": (30, ("human_confirm", "system_recheck", "tech_fix")),
    "subject_unconfirmed": (30, ("human_confirm", "system_recheck", "tech_fix")),
    "text_binding_failed": (30, ("human_confirm", "system_recheck", "tech_fix")),
    "ai_not_requested": (50, ("rerun_ai",)),
    "ai_partial_citation_rejected": (50, ("rerun_ai",)),
    "ai_partial_invalid_output": (50, ("rerun_ai",)),
    "ai_timeout": (50, ("rerun_ai",)),
    "candidate_amount_conflict": (100, ("human_confirm", "system_recheck", "tech_fix")),
    "comparison_identity_unresolved": (100, ("human_confirm", "system_recheck", "tech_fix")),
    "exception_amount_conflict": (100, ("human_confirm", "system_recheck", "tech_fix")),
    "exception_scope_ambiguous": (100, ("human_confirm", "system_recheck", "tech_fix")),
    "item_amount_not_included": (100, ("human_confirm", "system_recheck", "tech_fix")),
    "item_disclosure_missing": (100, ("human_confirm", "system_recheck", "tech_fix")),
    "item_sum_mismatch": (100, ("human_confirm", "system_recheck", "tech_fix")),
    "source_unit_normalized": (100, ("human_confirm", "system_recheck", "tech_fix")),
    "stated_total_missing": (100, ("human_confirm", "system_recheck", "tech_fix")),
    "sum_scope_not_comparable": (100, ("human_confirm", "system_recheck", "tech_fix")),
    "withheld_amount_unknown": (100, ("human_confirm", "system_recheck", "tech_fix")),
}

#: policy 的 recommended_actions 首选（与 actions 首项不同的照冻结建议）。
_RECOMMENDED: Dict[str, tuple] = {
    "operand_missing": ("system_recheck", "tech_fix", "human_confirm"),
    "operand_ambiguous": ("system_recheck", "tech_fix", "human_confirm"),
    "scope_unconfirmed": ("system_recheck", "tech_fix", "human_confirm"),
    "subject_conflict": ("system_recheck", "tech_fix", "human_confirm"),
    "subject_unconfirmed": ("system_recheck", "tech_fix", "human_confirm"),
    "subject_name_unlocated": ("system_recheck", "tech_fix", "human_confirm"),
    "text_binding_failed": ("system_recheck", "tech_fix", "human_confirm"),
    "row_code_missing": ("tech_fix",),
    "row_identity_duplicate": ("system_recheck", "tech_fix", "human_confirm"),
    "row_level_unresolved": ("tech_fix",),
    "cell_unbound": ("tech_fix",),
    "cell_low_confidence": ("system_recheck", "tech_fix", "human_confirm"),
    "cell_blank_semantics_unknown": ("system_recheck", "tech_fix", "human_confirm"),
    "column_identity_unconfirmed": ("tech_fix",),
    "column_width_drift": ("tech_fix",),
    "rule_no_reason": ("tech_fix",),
    "parse_error": ("tech_fix",),
    "execution_error": ("tech_fix",),
}


def allowed_actions(reason: Optional[str]) -> tuple:
    return REASON_POLICY.get(str(reason or ""), (30, ("human_confirm", "system_recheck", "tech_fix")))[1]


def canonical(value: Any) -> str:
    """与验收工具 canonical 逐字一致（排序键、紧凑分隔、default=str）。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def primary_blocker(blocked_by: Sequence[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    blockers = [dict(item) for item in blocked_by if isinstance(item, Mapping)]
    if not blockers:
        return None
    points = [{k: item.get(k) for k in ("dimension", "ref", "reason_code")} for item in blockers]
    return min(
        points,
        key=lambda item: (ROOT_PRIORITY.get(str(item.get("dimension")), 99), canonical(item)),
    )


def root_signature(item: Mapping[str, Any]) -> str:
    """冻结根因签名：主阻断点 > 已确认缺口位置 > 规则/子检查缺口规格。"""
    blockers = item.get("blocked_by") if isinstance(item.get("blocked_by"), list) else []
    point = primary_blocker(blockers)
    if point is not None:
        return canonical({"upstream": point})
    missing = item.get("missing_operands")
    if isinstance(missing, list) and missing and all(
        isinstance(value, dict)
        and type(value.get("page")) is int
        and value["page"] > 0
        and value.get("source") in {"table", "text"}
        for value in missing
    ):
        return canonical({"missing_locations": sorted(missing, key=canonical)})
    candidates = item.get("candidates")
    return canonical(
        {
            "rule": item.get("rule_id"),
            "sub_check": item.get("sub_check_id"),
            "obligation": item.get("obligation_id"),
            "reasons": sorted(str(code) for code in (item.get("reason_codes") or [])),
            "missing": missing,
            "candidate_locations": [
                {k: value.get(k) for k in ("source", "table_code", "section", "page", "cell_id", "span")}
                for value in (candidates if isinstance(candidates, list) else [])
                if isinstance(value, dict)
            ],
        }
    )


def root_cause_key(item: Mapping[str, Any]) -> str:
    return "rc:" + _sha256_text(root_signature(item))


def _policy_rank(code: str) -> tuple:
    priority = REASON_POLICY.get(code, (30, ()))[0]
    return (priority, code)


def _primary_code(codes: Sequence[str], blocked_by: Iterable[Mapping[str, Any]] = ()) -> str:
    """主原因码：优先取最上游阻断点，否则按冻结优先级取最小。"""
    point = primary_blocker(list(blocked_by or ()))
    if point is not None and str(point.get("reason_code")) in codes:
        return str(point["reason_code"])
    return min(codes, key=_policy_rank)


def choose_next_action(reason_codes: Iterable[str], blocked_by: Iterable[Mapping[str, Any]] = (),
                       preferred: Any = None) -> str:
    """按验收工具的同一口径确定主原因，再取 policy 建议的首个允许动作。

    ``preferred`` 只在该动作仍属于 policy 允许集合时生效：它用于表达
    “同一原因下用户该先做什么”的语义差异（例如原件格式非法优先回原件
    补正，而不是重跑系统），不改变冻结的 allowed_actions。
    """
    codes = [str(code) for code in reason_codes if str(code)]
    if not codes:
        return "human_confirm"
    primary = _primary_code(codes, blocked_by)
    allowed = allowed_actions(primary)
    if preferred and str(preferred) in allowed:
        return str(preferred)
    for action in _RECOMMENDED.get(primary, allowed):
        if action in allowed:
            return action
    return allowed[0]


def finalize(entry: Mapping[str, Any], *, rule_id: Any = None, sub_check_id: Any = None,
             obligation_id: Any = None) -> Dict[str, Any]:
    """把已登记的诊断事实补全为回执/义务可直接落库的五个字段。"""
    codes = list(dict.fromkeys(str(code) for code in (entry.get("reason_codes") or []) if str(code)))
    unknown = [code for code in codes if code not in FROZEN_REASON_CODES]
    if unknown:
        raise ValueError(f"未冻结的原因码：{unknown}")
    blocked_by = [dict(item) for item in (entry.get("blocked_by") or []) if isinstance(item, Mapping)]
    # 上游原因必须有对应阻断项；反向不得把非上游原因挂成阻断。
    for code in codes:
        dimensions = UPSTREAM_REASON_DIMENSIONS.get(code)
        if dimensions and not any(item.get("reason_code") == code for item in blocked_by):
            raise ValueError(f"上游原因 {code} 缺少阻断项")
    for item in blocked_by:
        code = str(item.get("reason_code") or "")
        if code not in UPSTREAM_REASON_DIMENSIONS:
            raise ValueError(f"阻断项原因码不是上游原因：{code}")
        if str(item.get("dimension")) not in UPSTREAM_REASON_DIMENSIONS[code]:
            raise ValueError(f"阻断项维度与原因码不符：{item}")
        if code not in codes:
            raise ValueError(f"阻断项原因码不在本条诊断中：{code}")
        if not str(item.get("ref") or "").strip():
            raise ValueError("阻断项 ref 不能为空")
    result: Dict[str, Any] = {
        "reason_codes": codes,
        "blocked_by": blocked_by,
        "missing_operands": [dict(item) for item in (entry.get("missing_operands") or []) if isinstance(item, Mapping)],
        "candidates": [dict(item) for item in (entry.get("candidates") or []) if isinstance(item, Mapping)],
    }
    if "operand_missing" in codes and not result["missing_operands"]:
        raise ValueError("operand_missing 必须给出 missing_operands")
    if "operand_ambiguous" in codes and len(result["candidates"]) < 2:
        raise ValueError("operand_ambiguous 必须给出至少两个候选位置")
    result["next_action"] = choose_next_action(codes, blocked_by, entry.get("preferred_action"))
    probe = dict(result)
    probe.update(rule_id=rule_id, sub_check_id=sub_check_id, obligation_id=obligation_id)
    result["root_cause_key"] = root_cause_key(probe)
    return result


# ---------------------------------------------------------------------------
# 文档级登记表：失败文本 → 结构化事实
# ---------------------------------------------------------------------------
#: 登记表挂在文档对象上，随文档一起释放；不跨文档、不落盘。
_REGISTRY_ATTR = "_diagnostic_failure_registry"
_REGISTRY_LOCK = threading.Lock()


def _registry(doc: Any, create: bool = False) -> Dict[str, List[Dict[str, Any]]]:
    bucket = getattr(doc, _REGISTRY_ATTR, None)
    if bucket is None:
        if not create:
            return {}
        bucket = {}
        try:
            setattr(doc, _REGISTRY_ATTR, bucket)
        except Exception:  # noqa: BLE001 - 只读/受限对象不登记，不影响规则结论
            return {}
    return bucket


def record_failure(doc: Any, texts: Any, entry: Mapping[str, Any]) -> None:
    """在失败产生处登记结构化事实；同一文本可对应多条（不同关系）。"""
    if doc is None:
        return
    if isinstance(texts, str):
        texts = [texts]
    clean = [str(text) for text in texts if str(text or "").strip()]
    if not clean:
        return
    bucket = _registry(doc, create=True)
    with _REGISTRY_LOCK:
        for text in clean:
            bucket.setdefault(text, []).append(dict(entry))


def lookup_failure(doc: Any, text: Any) -> List[Dict[str, Any]]:
    """按失败文本取回登记的结构化事实；先精确匹配，再按最长包含匹配。"""
    if doc is None or not str(text or "").strip():
        return []
    target = str(text)
    bucket = _registry(doc)
    if not bucket:
        return []
    with _REGISTRY_LOCK:
        if target in bucket:
            return [dict(item) for item in bucket[target]]
        hits: List[tuple] = []
        for known, entries in bucket.items():
            if len(known) >= 4 and known in target:
                hits.append((len(known), known))
        if not hits:
            return []
        hits.sort(reverse=True)
        matched: List[Dict[str, Any]] = []
        for _, known in hits:
            matched.extend(dict(item) for item in bucket[known])
        return matched


#: 规则级文本（没有更细证据可用时）按规则自己陈述的阻断类型归类。
#: 只用于规则执行摘要，不用于关系回执；ref 标明“规则级”，不伪造页码。
_RULE_LEVEL_PATTERNS = (
    (("结构化表模型不可用", "结构化表模型"), "table_mapping_incomplete", "instance_uid"),
    (("未定位到唯一的", "结构化表"), "table_missing", "instance_uid"),
    (("解析存在不确定",), "table_mapping_incomplete", "instance_uid"),
    (("金额单位未识别", "金额单位不一致"), "scope_unconfirmed", "fund_scope"),
    (("财政年度",), "scope_unconfirmed", "year"),
    (("列身份", "列组", "未绑定", "缺列", "列宽", "未登记「决算数」列", "列段内未定位", "未定位到功能分类"),
     "column_identity_unconfirmed", "column"),
    (("行身份", "行未", "重复行", "合计行", "层级", "科目编码"), "row_level_unresolved", "row"),
    (("身份不一致", "行内编码与名称", "编码与名称指向不同"), "row_level_unresolved", "row"),
    (("没有唯一当前事实",), "cell_unbound", "cell"),
    (("识别状态不可作操作数",), "cell_low_confidence", "cell"),
    (("缺少明确可用数值", "不能按零", "为横线", "分项有空白"), "cell_blank_semantics_unknown", "cell"),
    (("存在多个实例",), "table_mapping_incomplete", "instance_uid"),
    (("来源表仍有未完成读取",), "table_mapping_incomplete", "instance_uid"),
    (("识别风险阻断",), "cell_low_confidence", "cell"),
    (("评级类别未识别",), "row_level_unresolved", "row"),
    (("未取到", "取不到", "取数失败", "没有可用金额事实", "原文未定位"),
     "table_mapping_incomplete", "instance_uid"),
)

#: “说明里没有这段表述”类文本 → 用说明章节标题定位页码（找到才写，找不到写 null）。
_SECTION_HINTS = (
    ("基本支出", ("基本支出情况说明", "基本支出")),
    ("功能分类", ("支出决算情况说明", "功能分类")),
    ("人员经费", ("基本支出情况说明", "人员经费")),
    ("公用经费", ("基本支出情况说明", "公用经费")),
    ("财政拨款收支总计", ("财政拨款收支总表", "财政拨款")),
    ("决算情况说明", ("决算情况说明",)),
    ("三公", ("三公",)),
)
_DEFAULT_SECTION_HINTS = ("情况说明", "决算")


def _section_page(doc: Any, text: str) -> Optional[int]:
    hints: tuple = _DEFAULT_SECTION_HINTS
    for token, values in _SECTION_HINTS:
        if token in text:
            hints = values
            break
    pages = getattr(doc, "page_texts", None) or []
    for page_number, page in enumerate(pages, 1):
        content = str(page or "")
        if any(hint in content for hint in hints):
            return page_number
    return None


_TABLE_REF_RE = re.compile(r"第(\d+)页([A-Za-z_][A-Za-z0-9_]*)")


def rule_level_entry(text: Any, rule_id: Any = None, doc: Any = None) -> Dict[str, Any]:
    value = str(text or "")
    # 来源表映射失败：文本本身带“第N页表代码”，阻断位置直接取它，不降级成模糊规则级。
    if "来源表映射/读取未完成" in value:
        blockers = [
            {"dimension": "instance_uid", "ref": f"第{page}页{table}",
             "reason_code": "table_mapping_incomplete"}
            for page, table in dict.fromkeys(_TABLE_REF_RE.findall(value))
        ]
        if blockers:
            return {"reason_codes": ["table_mapping_incomplete"], "blocked_by": blockers,
                    "missing_operands": [], "candidates": []}
    for tokens, code, dimension in _RULE_LEVEL_PATTERNS:
        if any(token in value for token in tokens):
            return {
                "reason_codes": [code],
                "blocked_by": [{"dimension": dimension, "ref": f"规则级:{rule_id or 'unknown'}",
                                "reason_code": code}],
                "missing_operands": [],
                "candidates": [],
            }
    if any(token in value for token in ("未提取到", "未识别到", "未披露", "无法绑定", "未定位", "未找到")):
        page = _section_page(doc, value) if doc is not None else None
        return {
            "reason_codes": ["text_statement_absent"],
            "blocked_by": [],
            "missing_operands": [text_missing_operand(
                section="决算情况说明", role=value[:40] or "说明金额", page=page,
                row_key=None, column="说明表述", stage="决算")],
            "candidates": [],
        }
    return default_entry("insufficient_data")


def diagnostics_for_texts(doc: Any, texts: Iterable[Any], *, rule_id: Any = None,
                          sub_check_id: Any = None, obligation_id: Any = None) -> List[Dict[str, Any]]:
    """把多个失败文本对应的登记事实合并成可落库的诊断列表（按签名去重）。

    规则级摘要没有精确位置时：有已登记证据就用证据；一条都没登记时按规则
    自己陈述的阻断类型归类（规则级 ref），不把归不了类的文本塞进汇总。
    """
    merged: List[Dict[str, Any]] = []
    seen = set()
    for text in texts or []:
        for entry in lookup_failure(doc, text):
            try:
                finalized = finalize(
                    entry, rule_id=rule_id, sub_check_id=sub_check_id, obligation_id=obligation_id
                )
            except ValueError:
                continue
            signature = canonical(finalized)
            if signature in seen:
                continue
            seen.add(signature)
            merged.append(finalized)
    if merged:
        return merged
    for text in texts or []:
        if not str(text or "").strip():
            continue
        try:
            finalized = finalize(rule_level_entry(text, rule_id, doc), rule_id=rule_id,
                                 sub_check_id=sub_check_id, obligation_id=obligation_id)
        except ValueError:
            continue
        signature = canonical(finalized)
        if signature in seen:
            continue
        seen.add(signature)
        merged.append(finalized)
    return merged


def merge_entry(entry: Mapping[str, Any], extra: Mapping[str, Any]) -> Dict[str, Any]:
    """合并两条登记事实（用于同一关系的多个缺口）。"""
    result = {
        "reason_codes": list(dict.fromkeys(
            [str(code) for code in entry.get("reason_codes") or []]
            + [str(code) for code in extra.get("reason_codes") or []]
        )),
        "blocked_by": [dict(item) for item in entry.get("blocked_by") or []],
        "missing_operands": [dict(item) for item in entry.get("missing_operands") or []],
        "candidates": [dict(item) for item in entry.get("candidates") or []],
    }
    for field in ("blocked_by", "missing_operands", "candidates"):
        known = {canonical(item) for item in result[field]}
        for item in extra.get(field) or []:
            if canonical(item) not in known:
                result[field].append(dict(item))
                known.add(canonical(item))
    return result


def aggregate(diagnostics: Sequence[Mapping[str, Any]], *, rule_id: Any = None,
              sub_check_id: Any = None, obligation_id: Any = None) -> Optional[Dict[str, Any]]:
    """义务级汇总：原因码、阻断、缺数、候选都取全部回执的并集。

    根因键按汇总后的字段重新计算，保证与承载同一汇总的汇总回执同签名。
    """
    if not diagnostics:
        return None
    codes: List[str] = []
    blocked: List[Dict[str, Any]] = []
    missing: List[Dict[str, Any]] = []
    candidates: List[Dict[str, Any]] = []
    for diag in diagnostics:
        for code in diag.get("reason_codes") or []:
            if str(code) and str(code) not in codes:
                codes.append(str(code))
        for field, bucket in (("blocked_by", blocked), ("missing_operands", missing), ("candidates", candidates)):
            known = {canonical(item) for item in bucket}
            for item in diag.get(field) or []:
                if canonical(item) not in known:
                    bucket.append(dict(item))
                    known.add(canonical(item))
    if not codes:
        return None
    entry = {
        "reason_codes": codes,
        "blocked_by": blocked,
        "missing_operands": missing,
        "candidates": candidates,
    }
    if "operand_missing" in codes and not missing:
        # 汇总后缺数位置必须可交代；没有位置时退回规则级缺口规格，仍不编造页码。
        entry["reason_codes"] = [code for code in codes if code != "operand_missing"] or ["rule_no_reason"]
    if "operand_ambiguous" in codes and len(candidates) < 2:
        entry["reason_codes"] = [code for code in entry["reason_codes"] if code != "operand_ambiguous"] or ["rule_no_reason"]
    result: Dict[str, Any] = {
        "reason_codes": entry["reason_codes"],
        "blocked_by": blocked,
        "missing_operands": missing,
        "candidates": candidates,
    }
    preferences: Dict[str, str] = {}
    for diag in diagnostics:
        action = diag.get("preferred_action") or diag.get("next_action")
        codes_in_diag = [str(code) for code in (diag.get("reason_codes") or []) if str(code)]
        if not action or len(codes_in_diag) != 1:
            # 只有“本条诊断恰好只讲一个原因”时才继承它的动作，避免混合
            # 诊断把义务级路由带偏。
            continue
        preferences.setdefault(codes_in_diag[0], str(action))
    if result["reason_codes"]:
        primary = _primary_code(result["reason_codes"], blocked)
        result["next_action"] = choose_next_action(
            result["reason_codes"], blocked, preferences.get(primary))
    else:
        result["next_action"] = choose_next_action(result["reason_codes"], blocked)
    probe = dict(result)
    probe.update(rule_id=rule_id, sub_check_id=sub_check_id, obligation_id=obligation_id)
    result["root_cause_key"] = root_cause_key(probe)
    return result


def obligation_aggregate(obligation_id: str, diagnostics: Sequence[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    return aggregate(diagnostics, obligation_id=obligation_id)


def default_entry(status: Any) -> Dict[str, Any]:
    """没有登记到具体卡点时的诚实回退：解析/执行错误按状态，其余记过渡原因码。"""
    state = str(status or "")
    if state == "parse_error":
        return {"reason_codes": ["parse_error"], "blocked_by": [], "missing_operands": [], "candidates": []}
    if state == "execution_error":
        return {"reason_codes": ["execution_error"], "blocked_by": [], "missing_operands": [], "candidates": []}
    return {"reason_codes": ["rule_no_reason"], "blocked_by": [], "missing_operands": [], "candidates": []}


def _stamp_relation_identity(entry: Mapping[str, Any], rule_id: Any, sub_check_id: Any) -> Dict[str, Any]:
    """给缺数位置补真实检查关系身份（只补空字段，不改写已有取值）。

    J-01：义务覆盖必须能证明“同一检查关系/同一处事实”。关系 ID 由产生
    回执的规则与子检查决定，属于可核验的来源身份，不是从文字推断的标签。
    该字段随回执落库、汇总进义务并原样进入 API/导出，供覆盖判定核对。
    """
    result = dict(entry)
    rule = str(rule_id or "").strip()
    sub_check = str(sub_check_id or "").strip()
    relation = f"{rule}:{sub_check}" if rule and sub_check else ""
    operands = result.get("missing_operands")
    if not isinstance(operands, list):
        return result
    stamped: List[Dict[str, Any]] = []
    for operand in operands:
        if not isinstance(operand, Mapping):
            continue
        item = dict(operand)
        if relation:
            item.setdefault("check_relation_id", relation)
        if sub_check:
            item.setdefault("sub_check_id", sub_check)
        if rule:
            item.setdefault("rule_id", rule)
        stamped.append(item)
    result["missing_operands"] = stamped
    return result


def fields_for_text(doc: Any, text: Any, *, status: Any = "insufficient_data", rule_id: Any = None,
                   sub_check_id: Any = None, obligation_id: Any = None,
                   explicit_codes: Sequence[str] = (),
                   disclosure_sources: Sequence[Mapping[str, Any]] = ()) -> Dict[str, Any]:
    """一条回执的最终五个字段。

    优先使用失败发生处登记的结构化事实；规则显式传入的原因码只在仍能形成
    可定位诊断时并入（否则保留已登记的可定位诊断，避免把汇总变成不可解释）。
    """
    entries = lookup_failure(doc, text)
    if entries:
        base = entries[0]
        for extra in entries[1:]:
            base = merge_entry(base, extra)
    else:
        base = default_entry(status)
    candidates = [base]
    if explicit_codes:
        merged = merge_entry(base, {"reason_codes": list(explicit_codes)})
        if not merged.get("blocked_by") and not merged.get("missing_operands"):
            page = None
            for source in disclosure_sources or ():
                value = source.get("page") if isinstance(source, Mapping) else None
                if isinstance(value, int) and value > 0:
                    page = value
                    break
            if page is not None:
                merged["missing_operands"] = [text_missing_operand(
                    section="公开分项/说明", role="分项金额", page=page, column="金额", stage="决算")]
        candidates.insert(0, merged)
    for candidate in candidates:
        try:
            return finalize(_stamp_relation_identity(candidate, rule_id, sub_check_id),
                            rule_id=rule_id, sub_check_id=sub_check_id, obligation_id=obligation_id)
        except ValueError:
            continue
    return finalize(_stamp_relation_identity(default_entry(status), rule_id, sub_check_id),
                    rule_id=rule_id, sub_check_id=sub_check_id, obligation_id=obligation_id)


# ---------------------------------------------------------------------------
# 取数缺口登记：表格/文字两种来源的定位对象
# ---------------------------------------------------------------------------
def table_missing_operand(*, table_code: str, role: str, page: Optional[int], row_key: Any = None,
                          column: Optional[str] = None, fund_scope: Optional[str] = None,
                          stage: Optional[str] = None, year: Optional[int] = None) -> Dict[str, Any]:
    """缺数位置：source=table，表代码必须真实；页码未知写 null，不编造。"""
    return {
        "source": "table",
        "table_code": str(table_code),
        "role": str(role),
        "page": int(page) if isinstance(page, int) and page > 0 else None,
        "row_key": row_key if isinstance(row_key, (str, dict)) and row_key not in ("", {}) else None,
        "column": str(column) if column else None,
        "fund_scope": str(fund_scope) if fund_scope else None,
        "stage": str(stage) if stage else None,
        "year": int(year) if isinstance(year, int) and year >= 1900 else None,
    }


def text_missing_operand(*, section: str, role: str, page: Optional[int], row_key: Any = None,
                         column: Optional[str] = None, fund_scope: Optional[str] = None,
                         stage: Optional[str] = None, year: Optional[int] = None) -> Dict[str, Any]:
    """缺数位置：source=text，章节必须真实；页码未知写 null，不编造。"""
    return {
        "source": "text",
        "section": str(section),
        "role": str(role),
        "page": int(page) if isinstance(page, int) and page > 0 else None,
        "row_key": row_key if isinstance(row_key, (str, dict)) and row_key not in ("", {}) else None,
        "column": str(column) if column else None,
        "fund_scope": str(fund_scope) if fund_scope else None,
        "stage": str(stage) if stage else None,
        "year": int(year) if isinstance(year, int) and year >= 1900 else None,
    }


def table_candidate(*, table_code: str, page: int, cell_id: str, value: Any) -> Dict[str, Any]:
    return {
        "source": "table",
        "table_code": str(table_code),
        "page": int(page),
        "cell_id": str(cell_id),
        "value": value if isinstance(value, (str, int, float)) else None,
    }


def text_candidate(*, section: str, page: int, span: Sequence[int], value: Any) -> Dict[str, Any]:
    return {
        "source": "text",
        "section": str(section),
        "page": int(page),
        "span": [int(span[0]), int(span[1])],
        "value": value if isinstance(value, (str, int, float)) else None,
    }


def table_page(doc: Any, table_code: Any) -> Optional[int]:
    """表的起始物理页：只从已解析逻辑表实例读取，不按表种猜页码。"""
    artifact = getattr(doc, "parse_artifact", None)
    for instance in getattr(artifact, "logical_tables", []) or []:
        if str(getattr(instance, "table_code", "") or "") != str(table_code or ""):
            continue
        page = getattr(instance, "page_start", None)
        if isinstance(page, int) and page > 0:
            return page
    return None


def blocker(*, dimension: str, ref: str, reason_code: str) -> Dict[str, Any]:
    return {"dimension": str(dimension), "ref": str(ref), "reason_code": str(reason_code)}


def record_gap(doc: Any, texts: Any, *, reason_codes: Sequence[str],
               missing_operands: Sequence[Mapping[str, Any]] = (),
               candidates: Sequence[Mapping[str, Any]] = (),
               blocked_by: Sequence[Mapping[str, Any]] = (),
               preferred_action: Any = None) -> None:
    """登记一条取数缺口；字段组合由 finalize 校验，不合法则不登记（不影响结论）。"""
    entry = {
        "reason_codes": [str(code) for code in reason_codes],
        "blocked_by": [dict(item) for item in blocked_by],
        "missing_operands": [dict(item) for item in missing_operands],
        "candidates": [dict(item) for item in candidates],
    }
    if preferred_action:
        entry["preferred_action"] = str(preferred_action)
    try:
        finalize(entry)
    except ValueError:
        return
    record_failure(doc, texts, entry)


#: DH-03b：原因码 → 新文案短语（只用于未完成原因，不改变判定）。
_REASON_TEXT_LABELS = {
    "operand_missing": "缺少可核对金额",
    "operand_ambiguous": "存在多个候选位置，无法唯一绑定",
    "table_mapping_incomplete": "来源表映射/读取未完成",
    "table_missing": "未识别到目标表",
    "row_code_missing": "行缺少科目编码",
    "row_identity_duplicate": "行身份重复",
    "row_level_unresolved": "行层级未确定",
    "column_identity_unconfirmed": "金额列身份未确认",
    "column_width_drift": "跨页列宽/列身份变化",
    "cell_unbound": "单元格未绑定唯一事实",
    "cell_low_confidence": "单元格识别置信度低",
    "cell_blank_semantics_unknown": "空白/横线单元格含义未确认",
    "text_statement_absent": "说明缺少该表述",
    "text_binding_failed": "说明文字无法绑定",
    "text_statement_unparsed": "说明表述无法解析",
    "subject_unconfirmed": "主体口径未确认",
    "scope_unconfirmed": "统计口径未确认",
    "year_unconfirmed": "年度口径未确认",
    "parse_error": "解析失败",
    "execution_error": "执行异常",
    "rule_no_reason": "规则未给出具体原因",
    "ai_not_requested": "语义复核未执行",
    "ai_timeout": "语义复核超时",
    "withheld_amount_unknown": "暂不纳入的金额未知",
    "sum_scope_not_comparable": "加总范围不可比",
    "stated_total_missing": "未识别到说明总额",
    "item_sum_mismatch": "分项合计与总额不一致",
    "source_unit_normalized": "原文单位已折算",
}

#: DH-03b 明确要清理的旧文案。
_OLD_REASON_PHRASES = ("缺失或不唯一",)
_OLD_REASON_REPLACEMENT = "缺少可唯一核对的事实"


def _diagnostic_summary(entry: Mapping[str, Any]) -> str:
    """从结构化字段生成一句可读原因；没有结构化事实时返回空串。"""
    parts: list[str] = []
    blockers = entry.get("blocked_by") if isinstance(entry.get("blocked_by"), list) else []
    for blocker_item in blockers[:2]:
        if not isinstance(blocker_item, Mapping):
            continue
        code = str(blocker_item.get("reason_code") or "")
        label = _REASON_TEXT_LABELS.get(code, code or "上游阻断")
        ref = str(blocker_item.get("ref") or "").strip()
        parts.append(f"阻断 {ref}（{label}）" if ref else label)
    missing = entry.get("missing_operands") if isinstance(entry.get("missing_operands"), list) else []
    for operand in missing[:2]:
        if not isinstance(operand, Mapping):
            continue
        page = operand.get("page")
        source = str(operand.get("source") or "")
        place = str(operand.get("table_code") or operand.get("section") or "")
        label = f"缺数位置 第{page}页" if isinstance(page, int) and page > 0 else "缺数位置"
        parts.append(f"{label}{(' ' + source) if source else ''}{(':' + place) if place else ''}")
    candidates = entry.get("candidates") if isinstance(entry.get("candidates"), list) else []
    if len(candidates) >= 2 and not missing:
        parts.append(f"候选位置 {len(candidates)} 处")
    if not parts:
        codes = [str(code) for code in entry.get("reason_codes") or [] if str(code)]
        labels = [_REASON_TEXT_LABELS.get(code, code) for code in codes[:3]]
        if labels:
            parts.append("原因：" + "、".join(labels))
    return "；".join(dict.fromkeys(parts))


def render_reason_text(text: Any, entry: Mapping[str, Any], *, fallback: str = "") -> str:
    """DH-03b：未完成原因的新文案。

    - 清理旧文案短语，保留原有解释（不清空历史上下文）；
    - 追加由结构化字段生成的原因摘要；没有结构化事实时至少给出可读原因；
    - 永不返回空串。
    """
    value = str(text or "")
    for phrase in _OLD_REASON_PHRASES:
        value = value.replace(phrase, _OLD_REASON_REPLACEMENT)
    summary = _diagnostic_summary(entry)
    if summary and summary not in value:
        value = f"{value}；结构：{summary}" if value else f"结构：{summary}"
    value = value.strip()
    return value or (str(fallback or "").strip() or "检查未完成，需人工复核。")


def _reason_entry(item: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "reason_codes": list(item.get("reason_codes") or []),
        "blocked_by": list(item.get("blocked_by") or []),
        "missing_operands": list(item.get("missing_operands") or []),
        "candidates": list(item.get("candidates") or []),
    }


__all__ = [
    "FROZEN_REASON_CODES",
    "UPSTREAM_REASON_DIMENSIONS",
    "ROOT_PRIORITY",
    "STRUCTURAL_DIMENSIONS",
    "aggregate",
    "allowed_actions",
    "blocker",
    "canonical",
    "choose_next_action",
    "default_entry",
    "diagnostics_for_texts",
    "fields_for_text",
    "finalize",
    "lookup_failure",
    "merge_entry",
    "obligation_aggregate",
    "primary_blocker",
    "record_failure",
    "record_gap",
    "render_reason_text",
    "root_cause_key",
    "root_signature",
    "rule_level_entry",
    "table_candidate",
    "table_missing_operand",
    "table_page",
    "text_candidate",
    "text_missing_operand",
]
