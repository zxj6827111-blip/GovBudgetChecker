"""年度任务的组织边界、名册身份和统计纯函数。草稿不能充当应交事实。"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any

from src.schemas.material_slot import SlotIdentity
from src.services.material_ledger_query_service import relationship_of

DECISIONS = {"pending", "required", "not_applicable", "excluded"}


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def default_components(year: int) -> list[dict]:
    return [{"report_kind": "budget", "fiscal_year": year}, {"report_kind": "final", "fiscal_year": year - 1}]


def validate_components(components: list[dict]) -> list[dict]:
    if len(components) != 2 or {c.get("report_kind") for c in components} != {"budget", "final"}:
        raise ValueError("任务必须分别配置一项预算和一项决算")
    for c in components:
        if not isinstance(c.get("fiscal_year"), int) or not 2000 <= c["fiscal_year"] <= 2099:
            raise ValueError("财政年度须为2000—2099")
    return sorted([{"report_kind": c["report_kind"], "fiscal_year": c["fiscal_year"]} for c in components], key=lambda c: c["report_kind"])


def _ancestors(org_id: str, index: dict[str, dict]) -> list[dict]:
    result, seen = [], set()
    while org_id and org_id not in seen:
        seen.add(org_id)
        org = index.get(org_id)
        if org is None:
            break
        result.append(org)
        org_id = str(org.get("parent_id") or "")
    return result


def ancestors(org_id: str, catalog: list[dict]) -> list[dict]:
    return _ancestors(org_id, {str(o["id"]): o for o in catalog})

def jurisdiction(org_id: str, catalog: list[dict]) -> str | None:
    return next((str(o["id"]) for o in ancestors(org_id, catalog) if o.get("level") in {"city", "district"}), None)


def region_members(region_id: str, catalog: list[dict]) -> list[dict]:
    # 最近行政区划为该地区才属于本级；市节点不能跨越区县边界。
    index = {str(o["id"]): o for o in catalog}
    return [o for o in catalog if o.get("level") in {"department", "unit"} and next((str(p["id"]) for p in _ancestors(str(o["id"]), index) if p.get("level") in {"city", "district"}), None) == region_id]


def catalog_token(region_id: str, catalog: list[dict]) -> str:
    return digest(sorted([{k: o.get(k) for k in ("id", "name", "level", "parent_id")} for o in region_members(region_id, catalog)], key=lambda o: o["id"]))


def row_identity(row: dict) -> SlotIdentity:
    return SlotIdentity(str(row["subject_org_id"]), row["subject_kind"], row["material_scope"], row["report_kind"], int(row["fiscal_year"]))


def candidate_roster(region_id: str, catalog: list[dict], components: list[dict], previous: list[dict] | None = None) -> list[dict]:
    previous_by_key = {r["key"]: r for r in previous or []}
    rows = []
    index = {str(o["id"]): o for o in catalog}
    for org in region_members(region_id, catalog):
        chain = _ancestors(str(org["id"]), index)
        department = next((o for o in chain if o.get("level") == "department"), None)
        for component in validate_components(components):
            row = {"subject_org_id": str(org["id"]), "subject_org_name": org["name"], "subject_kind": org["level"],
                   "material_scope": "department_summary" if org["level"] == "department" else "unit_self",
                   "caliber": "summary" if org["level"] == "department" else "self", **component,
                   "jurisdiction_org_id": region_id, "department_org_id": str(department["id"]) if department else None,
                   "department_name": department["name"] if department else None, "decision": "pending", "reason": "", "due_at": None}
            row["key"] = row_identity(row).slot_key
            old = previous_by_key.get(row["key"])
            if old:
                for key in ("decision", "reason", "due_at"):
                    row[key] = old.get(key)
                # 名称或父级变化后需重新确认，不把旧决定套到新的目录结构上。
                if any(old.get(k) != row.get(k) for k in ("subject_org_name", "department_org_id", "department_name")):
                    row["decision"] = "pending"
            rows.append(row)
    # 消失的主体仍保留为待处理行，不能静默缩小名册。
    known = {r["key"] for r in rows}
    rows += [{**r, "catalog_missing": True} for r in previous or [] if r["key"] not in known]
    return sorted(rows, key=lambda r: (r.get("department_name") or "", r["subject_kind"] != "department", r["subject_org_name"], r["report_kind"], r["key"]))


def parse_due(value: Any) -> datetime | None:
    if not value:
        return None
    date = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if date.tzinfo is None:
        raise ValueError("截止时间必须包含时区")
    return date.astimezone(timezone.utc)


def roster_errors(rows: list[dict], catalog: list[dict], region_id: str, components: list[dict]) -> list[dict]:
    errors, seen = [], set()
    expected = {r["key"]: r for r in candidate_roster(region_id, catalog, components)}
    by_id = {str(o["id"]): o for o in catalog}
    aliases: dict[tuple, set[str]] = defaultdict(set)
    for row in rows:
        key = str(row.get("key") or "")
        reason = ""
        if key in seen:
            reason = "重复材料身份"
        seen.add(key)
        decision = row.get("decision")
        if decision not in DECISIONS or decision == "pending":
            reason = "尚未确认是否应交"
        elif decision in {"not_applicable", "excluded"} and not str(row.get("reason") or "").strip():
            reason = "不适用或排除必须说明原因"
        canonical = expected.get(key)
        if decision != "excluded":
            if canonical is None or any(row.get(k) != canonical.get(k) for k in ("subject_org_id", "subject_kind", "material_scope", "caliber", "subject_org_name", "department_org_id", "department_name", "jurisdiction_org_id", "fiscal_year", "report_kind")):
                reason = "主体或年度身份与当前目录不一致，请刷新草稿"
            try:
                parse_due(row.get("due_at"))
            except (ValueError, TypeError):
                reason = "截止时间不合法或未包含时区"
        if decision == "required" and canonical:
            org = by_id[canonical["subject_org_id"]]
            # 只提示同层同父级近似别名，不按名称归并业务主体。
            name = re.sub(r"[\s（）()]+", "", str(org["name"]))
            aliases[(org.get("parent_id"), org["level"], name)].add(str(org["id"]))
        if reason:
            errors.append({"key": key, "reason": reason})
    for key in expected.keys() - seen:
        errors.append({"key": key, "reason": "目录存在未纳入草稿的候选，请刷新草稿"})
    for ids in aliases.values():
        if len(ids) > 1:
            for r in rows:
                if r.get("decision") == "required" and r["subject_org_id"] in ids and not str(r.get("reason") or "").strip():
                    errors.append({"key": r["key"], "reason": "同层同父级存在疑似重复主体，请排除重复项或填写分别应交依据"})
    return errors


def summarize(items: list[dict], ready: bool, *, observed_items: list[dict] | None = None) -> dict:
    required = [r for r in items if r.get("decision") == "required"] if ready else []
    # 应交分母只能来自发布快照；未发布时，已有文件及其当前分析/复核事实仍可展示。
    operational = required if ready else (observed_items or [])
    uploaded = [r for r in operational if r["uploaded"]]
    analyzed = [r for r in operational if r.get("analyzed")]
    def subject_state(group: list[dict]) -> str:
        count = sum(r["uploaded"] for r in group)
        return "empty" if count == 0 else "complete" if count == len(group) else "partial"
    departments, subjects = defaultdict(list), defaultdict(list)
    for r in required:
        departments[r.get("department_id") or r["organization_id"]].append(r)
        if r["subject_kind"] == "unit":
            subjects[r["organization_id"]].append(r)
    return {"expected": len(required) if ready else None, "uploaded": len(uploaded) if ready or observed_items is not None else None,
            "missing": len(required) - len(uploaded) if ready else None,
            "overdue_missing": sum(bool(r.get("overdue")) for r in required) if ready else None,
            "due_unknown_missing": sum(not r["uploaded"] and not r.get("due_at") for r in required) if ready else None,
            "not_due_missing": sum(not r["uploaded"] and bool(r.get("due_at")) and not r.get("overdue") for r in required) if ready else None,
            "coverage_rate": len(uploaded) / len(required) if required else None,
            "department_count": len(departments) if ready else None, "unit_count": len(subjects) if ready else None,
            "empty_departments": sum(subject_state(g) == "empty" for g in departments.values()) if ready else None,
            "partial_departments": sum(subject_state(g) == "partial" for g in departments.values()) if ready else None,
            "complete_units": sum(subject_state(g) == "complete" for g in subjects.values()) if ready else None,
            "analyzed": len(analyzed), "reviewed": sum(r.get("review_status") == "completed" for r in operational),
            "confirmed": sum(r.get("confirmed_count") or 0 for r in analyzed) if analyzed else None,
            "pending": sum(r.get("pending_count") or 0 for r in analyzed) if analyzed else None,
            "pending_checks": sum(r.get("pending_checks") or 0 for r in analyzed) if analyzed else None,
            "unknown_analysis": sum(r.get("confirmed_count") is None for r in operational),
            "failed": sum(r.get("status") == "failed" for r in operational),
            "not_applicable": sum(r.get("decision") == "not_applicable" for r in items),
            "excluded": sum(r.get("decision") == "excluded" for r in items)}


def classify_item(snapshot: dict, current: dict | None, now: datetime) -> dict:
    item = {"slot_id": snapshot.get("slot_id"), "organization_id": snapshot["subject_org_id"],
            "organization_name": snapshot["subject_org_name"], "subject_kind": snapshot["subject_kind"],
            "department_id": snapshot.get("department_org_id"), "department_name": snapshot.get("department_name"),
            "report_kind": snapshot["report_kind"], "fiscal_year": snapshot["fiscal_year"],
            "decision": snapshot["decision"], "reason": snapshot.get("reason"), "due_at": snapshot.get("due_at"),
            "uploaded": False, "analyzed": False, "confirmed_count": None, "pending_count": None, "pending_checks": None,
            "findings": [], "review_status": "waiting_analysis", "status": "not_due", "job_id": None}
    if current:
        for key in ("uploaded", "analyzed", "confirmed_count", "pending_count", "pending_checks", "findings", "review_status", "status", "job_id", "filename", "document_version_id", "rounds_count", "history_incomplete", "_pending_findings"):
            if key in current:
                item[key] = current[key]
    due = parse_due(snapshot.get("due_at"))
    item["overdue"] = snapshot["decision"] == "required" and not item["uploaded"] and due is not None and now >= due
    item["relationship"] = relationship_of(subject_org_id=item["organization_id"], department_id=item["department_id"],
        subject_kind=item["subject_kind"], material_scope=snapshot["material_scope"], subject_org_name=item["organization_name"], department_name=item["department_name"])
    return item


def filter_items(items: list[dict], *, status: str, keyword: str, department_id: str, subject_id: str = "", baseline_ready: bool = True) -> list[dict]:
    # 仅看已上传文件不能判断谁缺交、逾期或已交齐，禁止从文件集合推导完整性。
    if not baseline_ready and status in {"empty", "partial", "complete", "unit_complete", "missing", "overdue"}:
        return []
    def match(r: dict) -> bool:
        return (not department_id or (r.get("department_id") or r["organization_id"]) == department_id) and (not subject_id or r["organization_id"] == subject_id) and (not keyword or keyword.casefold() in f"{r['organization_name']} {r.get('department_name') or ''}".casefold())
    selected = [r for r in items if match(r)]
    if status in {"empty", "partial", "complete"}:
        groups = defaultdict(list)
        for r in items:
            if r.get("decision") == "required":
                groups[r.get("department_id") or r["organization_id"]].append(r)
        ids = set()
        for key, rows in groups.items():
            received = sum(r["uploaded"] for r in rows)
            state = "empty" if received == 0 else "complete" if received == len(rows) else "partial"
            if state == status:
                ids.add(key)
        selected = [r for r in selected if (r.get("department_id") or r["organization_id"]) in ids]
    elif status == "received":
        selected = [r for r in selected if r["uploaded"]]
    elif status == "unit_complete":
        units = defaultdict(list)
        for r in items:
            if r.get("subject_kind") == "unit":
                units[r["organization_id"]].append(r)
        complete = {key for key, rows in units.items() if all(r["uploaded"] for r in rows)}
        selected = [r for r in selected if r["organization_id"] in complete]
    elif status == "missing":
        selected = [r for r in selected if r.get("decision") == "required" and not r["uploaded"]]
    elif status == "overdue":
        selected = [r for r in selected if r.get("overdue")]
    elif status == "issues":
        selected = [r for r in selected if r.get("confirmed_count")]
    elif status == "waiting":
        selected = [r for r in selected if r["uploaded"] and r["review_status"] != "completed"]
    elif status == "analyzed":
        selected = [r for r in selected if r.get("analyzed")]
    elif status == "reviewed":
        selected = [r for r in selected if r.get("review_status") == "completed"]
    return selected
