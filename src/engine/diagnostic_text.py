"""DH-03b：由结构化卡点生成说明文本。

只服务于诊断范围内的未完成回执/义务；正常说明类回执不经过本模块。
文本必须回答：核对什么、原文在哪、已取到什么、缺什么、为什么判断不了、
下一步由谁处理。禁止再出现“缺失或不唯一”这类合并句式。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

_DIMENSION_LABELS = {
    "organization": "材料主体",
    "year": "年度",
    "fund_scope": "资金口径",
    "budget_stage": "预算阶段",
    "org_scope": "汇总范围",
    "instance_uid": "表实例",
    "row": "行",
    "column": "列",
    "cell": "单元格",
}

_CODE_LABELS = {
    "operand_missing": "操作数缺失",
    "operand_ambiguous": "操作数存在多个候选",
    "row_code_missing": "科目编码未识别",
    "row_identity_duplicate": "行身份重复",
    "row_level_unresolved": "科目层级未定",
    "column_identity_unconfirmed": "列身份未确认",
    "column_width_drift": "跨页列宽漂移",
    "cell_blank_semantics_unknown": "空白格语义未知",
    "cell_low_confidence": "单元格识别置信度不足",
    "cell_unbound": "单元格未绑定",
    "table_missing": "缺表",
    "table_mapping_incomplete": "表映射不完整",
    "scope_unconfirmed": "汇总或资金口径未确认",
    "subject_unconfirmed": "材料主体未确认",
    "subject_conflict": "事实主体与材料主体不一致",
    "text_statement_unparsed": "疑似表述未解析出",
    "text_statement_absent": "原文未见对应表述",
    "text_binding_failed": "说明条目绑不到表内科目",
    "name_mismatch_confirmed": "名称不一致",
    "code_name_conflict": "编码与名称冲突",
    "subject_name_unlocated": "疑似旧科目名无法在文内定位",
    "rule_no_reason": "规则未给出具体卡点",
    "parse_error": "解析失败",
    "execution_error": "规则执行异常",
}

_ACTION_TEXT = {
    "system_recheck": "系统按上述位置定向补取",
    "tech_fix": "系统修复解析或规则后重跑",
    "human_confirm": "人工核对原文后确认",
    "rerun_ai": "补跑语义复核",
}


def _codes_text(codes: Iterable[str]) -> str:
    labels = [_CODE_LABELS.get(str(code), str(code)) for code in codes if str(code)]
    return "、".join(dict.fromkeys(labels)) or "规则未给出具体原因"


def _page_text(page: Any) -> str:
    return f"第{page}页" if type(page) is int and page > 0 else "页码未确认"


def _row_text(row_key: Any) -> Optional[str]:
    if isinstance(row_key, str) and row_key.strip():
        return row_key.strip()
    if isinstance(row_key, Mapping):
        parts = [str(value).strip() for value in row_key.values() if str(value).strip()]
        if parts:
            return " / ".join(parts)
    return None


def _missing_location(spec: Mapping[str, Any]) -> str:
    source = str(spec.get("source") or "")
    if source == "text":
        section = str(spec.get("section") or "说明章节")
        row = _row_text(spec.get("row_key"))
        role = str(spec.get("role") or "")
        detail = "、".join(part for part in (role, row) if part)
        return f"说明「{section}」{_page_text(spec.get('page'))}" + (f"（{detail}）" if detail else "")
    table_code = str(spec.get("table_code") or "表")
    row = _row_text(spec.get("row_key"))
    column = str(spec.get("column") or spec.get("role") or "")
    parts = [f"表{table_code}", _page_text(spec.get("page"))]
    if row:
        parts.append(f"行{row}")
    if column:
        parts.append(f"列{column}")
    return " / ".join(parts)


def _blocker_text(blocker: Mapping[str, Any]) -> str:
    dimension = _DIMENSION_LABELS.get(str(blocker.get("dimension")), str(blocker.get("dimension") or "上游"))
    return f"{dimension}（{blocker.get('ref') or '未识别到'}）"


def _candidate_text(candidate: Mapping[str, Any]) -> str:
    if str(candidate.get("source") or "") == "text":
        return f"说明「{candidate.get('section') or '未识别章节'}」{_page_text(candidate.get('page'))}"
    return f"表{candidate.get('table_code') or '未识别表'} {_page_text(candidate.get('page'))} 格{candidate.get('cell_id') or '未识别'}"


def _compat_reason(text: Any) -> str:
    """保留规则原有业务说明；只把禁止的合并句式换成中性表述。"""
    return str(text or "").strip().replace("缺失或不唯一", "未唯一取得")


def render_obligation_detail(
    instance: Mapping[str, Any],
    children: Sequence[Mapping[str, Any]] = (),
    old_detail: Optional[str] = None,
) -> str:
    title = str(instance.get("title") or instance.get("obligation_id") or "该检查项")
    codes = [str(code) for code in instance.get("reason_codes") or []]
    blocked = [item for item in instance.get("blocked_by") or [] if isinstance(item, dict)]
    missing = [item for item in instance.get("missing_operands") or [] if isinstance(item, dict)]
    candidates = [item for item in instance.get("candidates") or [] if isinstance(item, dict)]
    action = str(instance.get("next_action") or "")
    operand_count = sum(
        len(child.get("operand_fact_ids") or [])
        for child in children
        if isinstance(child, Mapping)
    )
    parts = [f"正在核对：{title}。"]
    where: List[str] = []
    where.extend(_missing_location(spec) for spec in missing)
    where.extend(_candidate_text(candidate) for candidate in candidates)
    where.extend(_blocker_text(blocker) for blocker in blocked)
    parts.append(f"原文位置：{'；'.join(dict.fromkeys(where))}。" if where else "原文位置：当前无法确认到具体页码或表格位置。")
    parts.append(
        f"已取到：{operand_count} 个操作数（逐项见关联回执）。"
        if operand_count
        else "已取到：没有可用操作数。"
    )
    if missing:
        parts.append(f"缺少：{'；'.join(_missing_location(spec) for spec in missing)}。")
    elif candidates:
        parts.append(f"候选：{'；'.join(_candidate_text(candidate) for candidate in candidates)}，需要人工消歧。")
    elif blocked:
        parts.append(f"上游未确认：{'；'.join(_blocker_text(blocker) for blocker in blocked)}。")
    else:
        parts.append("缺少：规则未给出可定位的操作数规格。")
    parts.append(f"判断不了的原因：{_codes_text(codes)}。")
    if codes == ["rule_no_reason"] and _compat_reason(old_detail):
        parts.append(f"规则说明：{_compat_reason(old_detail)}。")
    parts.append(f"下一步：{_ACTION_TEXT.get(action, action or '人工核对原文后确认')}。")
    return "".join(parts)


def render_receipt_reason(
    rule_id: str,
    sub_check_id: str,
    diagnosis: Mapping[str, Any],
    old_reason: Optional[str] = None,
) -> str:
    codes = [str(code) for code in diagnosis.get("reason_codes") or []]
    blocked = [item for item in diagnosis.get("blocked_by") or [] if isinstance(item, dict)]
    missing = [item for item in diagnosis.get("missing_operands") or [] if isinstance(item, dict)]
    candidates = [item for item in diagnosis.get("candidates") or [] if isinstance(item, dict)]
    action = str(diagnosis.get("next_action") or "")
    parts = [f"{rule_id} · {sub_check_id} 未完成。"]
    if blocked:
        parts.append(f"上游未确认：{'；'.join(_blocker_text(item) for item in blocked)}。")
    if missing:
        parts.append(f"缺少：{'；'.join(_missing_location(spec) for spec in missing)}。")
    if candidates:
        parts.append(f"候选：{'；'.join(_candidate_text(item) for item in candidates)}，需要人工消歧。")
    parts.append(f"判断不了的原因：{_codes_text(codes)}。")
    if codes == ["rule_no_reason"] and _compat_reason(old_reason):
        parts.append(f"规则说明：{_compat_reason(old_reason)}。")
    parts.append(f"下一步：{_ACTION_TEXT.get(action, action or '人工核对原文后确认')}。")
    return "".join(parts)


__all__ = ["render_obligation_detail", "render_receipt_reason"]
