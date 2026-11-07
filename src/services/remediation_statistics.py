"""R2.2 附页 A4：三维度统计（七类互斥作废）。

同一固定关系分母（= 义务台账 instances，不随人工操作变化），每关系保留
三个**独立**维度，分别聚合：

1. 引擎当前结果：完成通过 / 完成且发现问题 / 证据不适用 / 未知 / 未执行；
2. 变化归因（按定义计算，不靠人工状态覆盖）：本轮操作数恢复 / 新发现问题 /
   原件缺失 / 技术仍未解 / 范围外 / resolved_before_this_plan；
3. 人工处理状态：未处理 / 整项 verified_ok / verified_issue / not_applicable
   （轻路径无独立局部态）。

纪律（计划 A4）：
- 自动完成率读取**人工操作前**的引擎结果；人工处理后引擎结果不变、
  不从未解中消失——三维度独立聚合，任何一维都不修剪另外两维；
- 规则集交叉不直接相加；未实测不给百分比；
- 两个统计样例必须可复现（见 tests/remediation/test_r22_a4_statistics.py）：
  * 恢复后报错：引擎=完成且发现问题，归因=本轮操作数恢复+新发现问题，
    人工=未处理 → 同时计入自动恢复与自动发现问题；
  * 仍未知但人工已处理：引擎=未知，归因=技术仍未解，人工=verified_ok
    → 同时计入技术未解与人工已处理，自动化缺口不被掩盖。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

#: 维度一：引擎当前结果（固定互斥枚举）。
ENGINE_COMPLETED_PASS = "completed_pass"
ENGINE_COMPLETED_WITH_FINDINGS = "completed_with_findings"
ENGINE_NOT_APPLICABLE = "not_applicable"
ENGINE_UNKNOWN = "unknown"
ENGINE_NOT_EXECUTED = "not_executed"

#: 维度二：变化归因（按定义计算；一条关系可同时携带多个归因）。
ATTR_RECOVERED_THIS_ROUND = "recovered_this_round"
ATTR_NEW_FINDING = "new_finding"
ATTR_ORIGINAL_MATERIAL_MISSING = "original_material_missing"
ATTR_TECH_UNRESOLVED = "tech_unresolved"
ATTR_OUT_OF_SCOPE = "out_of_scope"
ATTR_RESOLVED_BEFORE_THIS_PLAN = "resolved_before_this_plan"

#: 维度三：人工处理状态（轻路径：无独立局部态）。
MANUAL_UNPROCESSED = "unprocessed"
MANUAL_VERIFIED_OK = "verified_ok"
MANUAL_VERIFIED_ISSUE = "verified_issue"
MANUAL_NOT_APPLICABLE = "not_applicable"

#: 台账状态 → 引擎维度。completed 依 has_confirmed_findings 二分。
_LEDGER_STATUS_TO_ENGINE: Dict[str, str] = {
    "completed": ENGINE_COMPLETED_PASS,
    "not_applicable": ENGINE_NOT_APPLICABLE,
    "insufficient_data": ENGINE_UNKNOWN,
    "not_executed": ENGINE_NOT_EXECUTED,
    "parse_error": ENGINE_UNKNOWN,
    "execution_error": ENGINE_UNKNOWN,
}


def engine_dimension(status: str, *, has_confirmed_findings: bool = False) -> str:
    """台账状态 + 是否携带已确认 finding → 引擎当前结果。"""
    if status == "completed" and has_confirmed_findings:
        return ENGINE_COMPLETED_WITH_FINDINGS
    return _LEDGER_STATUS_TO_ENGINE.get(str(status or ""), ENGINE_UNKNOWN)


def engine_from_instance(instance: Mapping[str, Any]) -> str:
    """台账 instance（dict）→ 引擎维度。

    输入契约：``findings_count``/``has_confirmed_findings`` 由**报告层**在
    组装 relations 时提供（把规则结果里该义务 checkers 的已确认 finding
    数对回关系）；当前义务台账 dict（ObligationInstance.to_dict）本身不含
    该字段——台账只有引擎状态，"完成且发现问题"的二分依据在报告侧组装，
    不在本模块猜。两个字段都缺省时 completed 一律映射为 completed_pass。
    """
    findings = instance.get("findings_count")
    has_findings = bool(findings) or bool(instance.get("has_confirmed_findings"))
    return engine_dimension(str(instance.get("status", "")), has_confirmed_findings=has_findings)


def derive_attribution(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    out_of_scope_ids: Iterable[str] = (),
) -> List[str]:
    """按定义从修复前/后台账实例推导单条关系的变化归因（不读人工状态）。

    - 修复前已完成 → resolved_before_this_plan（本轮不计量）；
    - 修复前未完成、修复后完成且发现问题 → 本轮操作数恢复 + 新发现问题；
    - 修复前未完成、修复后完成通过 → 本轮操作数恢复；
    - 修复前后都未完成 → 技术仍未解；标记范围外的 → 范围外（附加）；
    - 修复前未完成、修复后缺失（无记录）→ 原件缺失。
    """
    before_status = str(before.get("status", "")) if before else ""
    after_status = str(after.get("status", "")) if after else ""
    before_done = before_status == "completed" or before_status == "not_applicable"
    if before_done:
        return [ATTR_RESOLVED_BEFORE_THIS_PLAN]
    attributions: List[str] = []
    if not after:
        attributions.append(ATTR_ORIGINAL_MATERIAL_MISSING)
        return attributions
    after_done = after_status == "completed" or after_status == "not_applicable"
    after_findings = bool(after.get("findings_count")) or bool(after.get("has_confirmed_findings"))
    if after_done:
        attributions.append(ATTR_RECOVERED_THIS_ROUND)
        if after_findings:
            attributions.append(ATTR_NEW_FINDING)
    else:
        attributions.append(ATTR_TECH_UNRESOLVED)
    if after.get("obligation_id") and str(after["obligation_id"]) in set(out_of_scope_ids):
        attributions.append(ATTR_OUT_OF_SCOPE)
    return attributions


def aggregate_three_dimensions(
    relations: Sequence[Mapping[str, Any]],
    *,
    manual_decisions: Optional[Mapping[str, Optional[str]]] = None,
) -> Dict[str, Any]:
    """三维度独立聚合。

    relations：固定分母，每条至少含 relation_id 与引擎维度（engine_dimension
    的返回值）；manual_decisions：relation_id → 人工决定（缺省 = 未处理）。
    返回三个独立计数块 + 关系级明细（供报告与复核）；不产生百分比。
    """
    manual_decisions = manual_decisions or {}
    engine_counts: Dict[str, int] = {
        ENGINE_COMPLETED_PASS: 0,
        ENGINE_COMPLETED_WITH_FINDINGS: 0,
        ENGINE_NOT_APPLICABLE: 0,
        ENGINE_UNKNOWN: 0,
        ENGINE_NOT_EXECUTED: 0,
    }
    attribution_counts: Dict[str, int] = {key: 0 for key in (
        ATTR_RECOVERED_THIS_ROUND,
        ATTR_NEW_FINDING,
        ATTR_ORIGINAL_MATERIAL_MISSING,
        ATTR_TECH_UNRESOLVED,
        ATTR_OUT_OF_SCOPE,
        ATTR_RESOLVED_BEFORE_THIS_PLAN,
    )}
    manual_counts: Dict[str, int] = {
        MANUAL_UNPROCESSED: 0,
        MANUAL_VERIFIED_OK: 0,
        MANUAL_VERIFIED_ISSUE: 0,
        MANUAL_NOT_APPLICABLE: 0,
    }
    detail: List[Dict[str, Any]] = []
    for relation in relations:
        relation_id = str(relation.get("relation_id", ""))
        engine = str(relation.get("engine_dimension")
                     or engine_dimension(str(relation.get("status", "")),
                                         has_confirmed_findings=bool(relation.get("findings_count"))))
        if engine not in engine_counts:
            engine = ENGINE_UNKNOWN
        engine_counts[engine] += 1
        attributions = [str(a) for a in (relation.get("attributions") or []) if str(a) in attribution_counts]
        for attribution in attributions:
            attribution_counts[attribution] += 1
        decision = manual_decisions.get(relation_id)
        manual = decision if decision in (MANUAL_VERIFIED_OK, MANUAL_VERIFIED_ISSUE, MANUAL_NOT_APPLICABLE) else MANUAL_UNPROCESSED
        manual_counts[manual] += 1
        detail.append({
            "relation_id": relation_id,
            "engine_dimension": engine,
            "attributions": attributions,
            "manual_status": manual,
        })
    return {
        "denominator": len(relations),
        "engine": engine_counts,
        "attribution": attribution_counts,
        "manual": manual_counts,
        "relations": detail,
        "notes": [
            "三维度独立聚合：人工处理不改变引擎结果与变化归因。",
            "自动完成率读取人工操作前的引擎结果；未实测不给百分比。",
        ],
    }
