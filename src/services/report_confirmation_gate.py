#!/usr/bin/env python
"""整份确认门禁与报告三分类（计划 D4）。

报告必须清晰区分三类对象，不能混在一张"问题清单"里：
1. ``confirmed_material_issues``——已确定的材料问题：证据校验通过的规则
   finding（当前分析版本、证据充分）。
2. ``genuine_ambiguities``——需要判断的真实材料歧义：severity=manual_review、
   OCR/识别风险（证据 unverified）或诊断标记 parse_ambiguity 的未决项。
3. ``system_incomplete``——系统尚未完成的检查及具体卡点：next_action 为
   system_recheck / tech_fix 的未决规则、AI 未执行、证据链失败的 finding。

整份确认（confirm_report）门禁：
- 默认关闭（``confirm_report_enabled=False``）；开启后也只签认当前分析版本
  中证据充分的确定性问题。
- ``can_confirm_whole_report``：不存在 system_incomplete 项时为 True——
  系统失败不能要求用户代替程序重新计算，也不能把未计算的关系变成已完成。
- 确认范围绑定分析版本（analysis_revision），不覆盖人工疑问、忽略决定和
  历史有效结论。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

_AMBIGUITY_MARKERS = ("parse_ambiguity", "歧义", "需人工复核", "待人工复核")
_SYSTEM_NEXT_ACTIONS = {"system_recheck", "tech_fix", "system_fix"}


def _finding_evidence_status(finding: Dict[str, Any]) -> str:
    verification = finding.get("evidence_verification")
    if isinstance(verification, dict):
        return str(verification.get("status") or "unverified")
    return "unverified"


def _unresolved_next_action(entry: Dict[str, Any]) -> str:
    diagnostics = entry.get("diagnostics") or []
    for diagnostic in diagnostics:
        action = str((diagnostic or {}).get("next_action") or "").strip()
        if action:
            return action
    return ""


def build_report_confirmation(
    status: Dict[str, Any],
    *,
    confirm_report_enabled: bool = False,
) -> Dict[str, Any]:
    """从任务 status.json 组装整份确认门禁与三分类。纯读取，不落盘。"""
    result = status.get("result") or {}
    meta = result.get("meta") or {}
    findings = result.get("rule_findings") or []
    ai_findings = result.get("ai_findings") or []
    coverage = meta.get("obligation_coverage") or status.get("check_coverage") or {}
    review_reasons = status.get("review_reasons") or []

    confirmed: List[Dict[str, Any]] = []
    ambiguities: List[Dict[str, Any]] = []
    system_incomplete: List[Dict[str, Any]] = []

    for finding in list(findings) + list(ai_findings):
        entry = {
            "rule_id": finding.get("rule_id") or finding.get("source"),
            "severity": finding.get("severity"),
            "page": (finding.get("location") or {}).get("page"),
            "message": str(finding.get("message") or "")[:200],
        }
        evidence_status = _finding_evidence_status(finding)
        if evidence_status == "verified":
            confirmed.append(entry)
        elif evidence_status == "failed":
            system_incomplete.append({**entry, "reason": "evidence_delivery_failed"})
        elif evidence_status == "unverified":
            ambiguities.append({**entry, "reason": "evidence_unverified"})
        if str(finding.get("severity") or "").lower() == "manual_review" and entry not in ambiguities:
            ambiguities.append({**entry, "reason": "manual_review"})

    unresolved_rules: List[Dict[str, Any]] = []
    for reason in review_reasons:
        for entry in (reason.get("unresolved_rules") or []):
            unresolved_rules.append(entry)
    seen_keys = set()
    for entry in unresolved_rules:
        rule_id = str(entry.get("rule_id") or "")
        detail = str(entry.get("detail") or "")
        key = (rule_id, detail[:80])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        item = {
            "rule_id": rule_id,
            "detail": detail[:300],
            "next_action": _unresolved_next_action(entry),
        }
        if any(marker in detail for marker in _AMBIGUITY_MARKERS):
            ambiguities.append({**item, "reason": "parse_ambiguity_or_manual"})
        else:
            system_incomplete.append({**item, "reason": "insufficient_data"})

    # 义务级未完成（OBL-* 实例）：AI 未执行/取数不足按系统侧记账
    for instance in (coverage.get("instances") or []):
        instance_status = str(instance.get("status") or "")
        obligation_id = str(instance.get("obligation_id") or "")
        if instance_status in {"completed", "not_applicable"}:
            continue
        if instance_status == "ai_not_run":
            system_incomplete.append({
                "rule_id": obligation_id,
                "detail": "语义模型未执行（ai_not_run）",
                "next_action": "ai_not_run",
                "reason": "ai_not_run",
            })
        elif instance_status == "insufficient_data":
            # 细粒度原因已由未决规则逐条登记，义务级只补未挂规则的空档
            if not any(str(item.get("rule_id")) == obligation_id for item in system_incomplete + ambiguities):
                system_incomplete.append({
                    "rule_id": obligation_id,
                    "detail": "应检查事项取数不足",
                    "next_action": "system_recheck",
                    "reason": "insufficient_data",
                })

    return {
        "analysis_revision": status.get("analysis_revision"),
        "conclusion": status.get("analysis_conclusion"),
        "confirm_report_enabled": bool(confirm_report_enabled),
        "gate": {
            "can_confirm_whole_report": len(system_incomplete) == 0,
            "blocking_reason": (
                None if not system_incomplete
                else f"存在 {len(system_incomplete)} 项系统未完成检查，不能整份签认"
            ),
            "confirm_scope": "deterministic_findings_only",
        },
        "categories": {
            "confirmed_material_issues": confirmed,
            "genuine_ambiguities": ambiguities,
            "system_incomplete": system_incomplete,
        },
        "counts": {
            "confirmed_material_issues": len(confirmed),
            "genuine_ambiguities": len(ambiguities),
            "system_incomplete": len(system_incomplete),
        },
    }


def confirm_report_allowed(
    confirmation: Dict[str, Any],
    current_revision: Optional[str],
) -> Dict[str, Any]:
    """写侧门禁：默认关闭/系统未完成/版本漂移都不放行。"""
    if not confirmation.get("confirm_report_enabled"):
        return {"allowed": False, "code": "CONFIRM_REPORT_DISABLED"}
    gate = confirmation.get("gate") or {}
    if not gate.get("can_confirm_whole_report"):
        return {"allowed": False, "code": "SYSTEM_INCOMPLETE"}
    revision = confirmation.get("analysis_revision")
    if current_revision and revision and str(current_revision) != str(revision):
        return {"allowed": False, "code": "REVISION_CHANGED"}
    return {"allowed": True, "code": "OK"}
