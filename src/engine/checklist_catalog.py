"""检查点清单目录：清单条目 → 检查义务 → 规则编号（2026-10-22 起全系统默认）。

清单文件在 ``rules/checklists/``：每条检查点声明覆盖状态（implemented / partial /
ai_assisted / external）、对应的检查义务编号与规则编号。未被规则完全覆盖的条目
（partial / ai_assisted / external）还须带处置决议 ``decision``：closed=按决议不再
自动化，deferred=暂缓待条件具备，ai_only=规则只作提示、进一步判断只交大模型 AI。
本模块负责加载、自检，并给出覆盖统计，供 ``/api/rules/checklists`` 与测试使用。
清单不按地区区分——用户决议（2026-10-22）：普陀区清单直接作为整个系统的检查指标。
"""
from __future__ import annotations

from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

CHECKLIST_DIR = Path(__file__).resolve().parents[2] / "rules" / "checklists"
CHECKLIST_FILES: Dict[str, str] = {
    "budget": "budget_checklist_v1.yaml",
    "final": "final_checklist_v1.yaml",
}
CHECKLIST_STATUSES = ("implemented", "partial", "ai_assisted", "external")
#: 未被规则完全覆盖的条目必须带的处置决议；清单里不允许存在“还没想好”的条目。
CHECKLIST_DECISIONS = ("closed", "deferred", "ai_only")


@lru_cache(maxsize=None)
def load_checklist(report_kind: str) -> Dict[str, Any]:
    path = CHECKLIST_DIR / CHECKLIST_FILES[report_kind]
    with path.open("r", encoding="utf-8") as handle:
        document = yaml.safe_load(handle) or {}
    document["items"] = list(document.get("items") or [])
    return document


def validate_checklist(report_kind: str) -> List[str]:
    """清单自检：编号唯一、状态合法、义务存在且适用本文种、规则已注册到本文种。"""
    from .check_obligations import OBLIGATION_CATALOG
    from .rule_registry import registered_rule_ids

    obligations = {item.obligation_id: item for item in OBLIGATION_CATALOG}
    registered = registered_rule_ids(report_kind)
    document = load_checklist(report_kind)
    problems: List[str] = []
    if document.get("report_kind") != report_kind:
        problems.append(f"清单文种声明为 {document.get('report_kind')}，应为 {report_kind}")
    seen: Counter = Counter(item.get("id") for item in document["items"])
    problems += [f"{cid}: 编号重复 {n} 次" for cid, n in seen.items() if n > 1]
    for item in document["items"]:
        cid = item.get("id")
        status = item.get("status")
        if status not in CHECKLIST_STATUSES:
            problems.append(f"{cid}: 未知状态 {status}")
        if status in {"partial", "ai_assisted", "external"} and not item.get("note"):
            problems.append(f"{cid}: 状态为 {status} 但缺少 note 说明未覆盖部分")
        decision = item.get("decision")
        if status == "implemented":
            if decision:
                problems.append(f"{cid}: 已覆盖条目不应带处置决议 {decision}")
        elif decision not in CHECKLIST_DECISIONS:
            problems.append(f"{cid}: 状态为 {status} 但处置决议 {decision} 不合法")
        elif status == "ai_assisted" and decision != "ai_only":
            problems.append(f"{cid}: 词表+AI 复核条目的处置决议应为 ai_only")
        if status != "external" and not item.get("obligations"):
            problems.append(f"{cid}: 非 external 条目必须映射至少一条检查义务")
        for obligation_id in item.get("obligations") or []:
            obligation = obligations.get(obligation_id)
            if obligation is None:
                problems.append(f"{cid}: 检查义务 {obligation_id} 不存在")
            elif report_kind not in obligation.report_kinds:
                problems.append(f"{cid}: 检查义务 {obligation_id} 不适用于 {report_kind}")
        for rule_id in item.get("rules") or []:
            if rule_id not in registered:
                problems.append(f"{cid}: 规则 {rule_id} 未注册到 {report_kind} 规则集")
    return problems


def checklist_coverage(report_kind: str) -> Dict[str, Any]:
    document = load_checklist(report_kind)
    counts = Counter(item.get("status") for item in document["items"])
    decisions = Counter(item.get("decision") for item in document["items"] if item.get("decision"))
    return {
        "report_kind": report_kind,
        "version": document.get("version"),
        "effective_date": str(document.get("effective_date") or ""),
        "scope": document.get("scope"),
        "total": len(document["items"]),
        "by_status": {status: counts.get(status, 0) for status in CHECKLIST_STATUSES},
        "by_decision": {decision: decisions.get(decision, 0) for decision in CHECKLIST_DECISIONS},
        "items": [
            {
                "id": item.get("id"),
                "dimension": item.get("dimension"),
                "title": item.get("title"),
                "status": item.get("status"),
                "decision": item.get("decision"),
                "obligations": list(item.get("obligations") or []),
                "rules": list(item.get("rules") or []),
                "note": item.get("note"),
            }
            for item in document["items"]
        ],
    }


def checklist_items_for_rule(rule_id: str) -> List[Tuple[str, str]]:
    """规则编号 → [(文种, 清单编号)]，供问题清单回挂清单条目。"""
    target = str(rule_id or "").strip().upper()
    hits: List[Tuple[str, str]] = []
    for kind in CHECKLIST_FILES:
        for item in load_checklist(kind)["items"]:
            if target in {str(r).upper() for r in item.get("rules") or []}:
                hits.append((kind, str(item.get("id"))))
    return hits
