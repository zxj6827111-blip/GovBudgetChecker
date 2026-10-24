"""主管端：全部统计与导出在服务端按组织范围过滤。"""

import csv
import hashlib
import io
import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from api import material_access, runtime
from api.auth_utils import require_login, user_can_access_org
from src.services.audit_log import append_audit_event
from src.services.supervision_metrics import json_value
from src.services.supervision_query import available_periods, load_rows, material_detail, query
from src.services.review_session_store import build_analysis_basis_token

router = APIRouter()


def context(request: Request, scope_id: str = ""):
    _, _, user = require_login(request)
    access = material_access.build_access_scope(user)
    if not access.unrestricted and not access.has_any_visible_org:
        raise HTTPException(403, "当前账号未配置主管组织范围，请联系管理员")
    storage = runtime.require_org_storage()
    records = list(storage.get_all())
    visible = [r for r in records if user_can_access_org(user, str(r.id))]
    if scope_id:
        target = storage.get_by_id(scope_id)
        if not target or not user_can_access_org(user, scope_id):
            raise HTTPException(403, "无权查看该组织范围")
        descendants = {scope_id}
        changed = True
        while changed:
            added = {str(r.id) for r in records if str(r.parent_id or "") in descendants}
            changed = not added.issubset(descendants)
            descendants |= added
        scope_ids = list(descendants)
    else:
        scope_ids = None
    organizations = [{"id": str(r.id), "name": r.name, "level": r.level, "parent_id": r.parent_id} for r in visible]
    return user, access, scope_ids, organizations


def baseline_token(rows: list[dict]) -> str:
    entries = sorted({(r["slot_id"], r["subject_org_id"], r["applicability_status"], r.get("mapping_key") or "") for r in rows})
    return hashlib.sha256(json.dumps(entries, ensure_ascii=False).encode()).hexdigest()


async def with_connection(callback):
    return await material_access.with_connection(callback)


async def with_read_snapshot(callback):
    async def read(conn):
        # 一次响应的版本指针、复核和整改记录使用同一数据库快照。
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            return await callback(conn)
    return await with_connection(read)


@router.get("/api/supervision")
async def get_supervision(request: Request, fiscal_year: int = Query(ge=2000, le=2099),
    report_kind: Literal["budget", "final"] = "budget", scope_id: str = "",
    view: Literal["overview", "materials", "departments", "issues", "remediation"] = "overview",
    keyword: str = Query("", max_length=120), department_id: str = "",
    status: Literal["all", "missing", "overdue", "repeat", "issues", "waiting"] = "all",
    severity: Literal["all", "critical", "high", "medium", "low", "manual_review"] = "all",
    category: str = Query("", max_length=100), sort: Literal["confirmed", "rate", "severe"] = "confirmed",
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=50), format: Literal["json", "csv"] = "json"):
    user, access, scope_ids, organizations = context(request, scope_id)
    async def read(conn):
        result = await query(conn, visible_org_ids=access.visible_org_ids, scope_ids=scope_ids, scope_id=scope_id,
            fiscal_year=fiscal_year, report_kind=report_kind, view=view, keyword=keyword, department_id=department_id,
            status=status, severity=severity, category_filter=category, sort=sort, page=page, page_size=page_size,
            export=format == "csv")
        if not user.get("is_read_only") and scope_id:
            rows = await load_rows(conn, visible_org_ids=access.visible_org_ids, scope_ids=scope_ids,
                                   fiscal_year=fiscal_year, report_kind=report_kind)
            result["meta"]["baseline_preview_token"] = baseline_token(rows)
        result["meta"].update({"generated_at": datetime.now(timezone.utc).isoformat(), "is_read_only": bool(user.get("is_read_only")),
                               "organizations": organizations,
                               "available_periods": await available_periods(conn, visible_org_ids=access.visible_org_ids, scope_ids=scope_ids)})
        return result
    data = await with_read_snapshot(read)
    if format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["统计时间", data["meta"]["generated_at"], "财政年度", fiscal_year, "文种", report_kind])
        writer.writerow(["统计口径", "已确认应交清单" if data["meta"]["baseline_ready"] else "仅已建档材料，应交总量未知"])
        rows = data.pop("export_rows", [])
        columns = ["name", "organization_name", "department_name", "title", "expected", "uploaded", "confirmed", "confirmed_count", "pending_count", "rounds_count", "review_status", "due_at"]
        labels = ["部门", "主体单位", "主管部门", "问题", "应交份数", "已交", "部门确认问题", "材料确认问题", "待复核", "登记整改轮次", "复核状态", "截止时间"]
        writer.writerow(labels)
        def safe(value):
            if value is None:
                return "未知"
            text = str(value)
            # 名称/问题文本可能由用户上传，避免 CSV 公式注入。
            return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text
        for row in rows:
            writer.writerow([safe(row.get(key)) for key in columns])
        return Response("\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="supervision.csv"'})
    data.pop("export_rows", None)
    return {"ok": True, "data": data}


class NotedRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class BaselineRequest(NotedRequest):
    scope_id: str
    fiscal_year: int = Field(ge=2000, le=2099)
    report_kind: Literal["budget", "final"]
    preview_token: str
    note: str = Field(min_length=4, max_length=2000)


@router.post("/api/supervision/baselines")
async def confirm_baseline(request: Request, body: BaselineRequest):
    user, access, scope_ids, _ = context(request, body.scope_id)
    if not body.scope_id:
        raise HTTPException(422, "请指定组织范围")
    async def write(conn):
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", f"supervision:{body.scope_id}:{body.fiscal_year}:{body.report_kind}")
            rows = await load_rows(conn, visible_org_ids=access.visible_org_ids, scope_ids=scope_ids, fiscal_year=body.fiscal_year, report_kind=body.report_kind)
            if not rows or any(r.get("mapping_key") or r.get("caliber_conflict_candidate") or r["applicability_status"] == "unresolved" for r in rows):
                raise HTTPException(409, "清单为空或仍有身份/口径待确认材料，请先完善材料台账")
            if baseline_token(rows) != body.preview_token:
                raise HTTPException(409, "应交清单已变化，请刷新后重新确认")
            ids = sorted({r["slot_id"] for r in rows})
            await conn.execute("INSERT INTO supervision_baselines(scope_org_id,fiscal_year,report_kind,slot_ids,confirmed_by,note) VALUES($1,$2,$3,$4::jsonb,$5,$6)",
                body.scope_id, body.fiscal_year, body.report_kind, json.dumps(ids), user["username"], body.note)
            return {"count": len(ids)}
    result = await with_connection(write)
    append_audit_event(action="supervision.baseline.confirm", actor=user["username"], result="success", resource_type="organization", resource_id=body.scope_id)
    return {"ok": True, "data": result}


async def accessible_detail(conn, slot_id, access):
    row = await conn.fetchrow("SELECT * FROM material_slots WHERE id::text=$1", slot_id)
    material_access.require_slot_access(access, dict(row) if row else None, slot_id)
    return await material_detail(conn, slot_id, access.visible_org_ids)


@router.get("/api/supervision/materials/{slot_id}")
async def get_detail(request: Request, slot_id: str):
    user, access, _, _ = context(request)
    detail = await with_read_snapshot(lambda conn: accessible_detail(conn, slot_id, access))
    return {"ok": True, "data": {**detail, "is_read_only": bool(user.get("is_read_only"))}}


class RoundRequest(NotedRequest):
    document_version_id: int
    note: str = Field(min_length=4, max_length=2000)


@router.post("/api/supervision/materials/{slot_id}/rounds")
async def register_round(request: Request, slot_id: str, body: RoundRequest):
    user, access, _, _ = context(request)
    async def write(conn):
        async with conn.transaction():
            row = await conn.fetchrow("SELECT * FROM material_slots WHERE id::text=$1 FOR UPDATE", slot_id)
            row = material_access.require_slot_access(access, dict(row) if row else None, slot_id)
            if row["current_document_version_id"] != body.document_version_id:
                raise HTTPException(409, "当前材料版本已变化，请刷新")
            prior = await conn.fetchrow("SELECT id,review_result,document_version_id,completed_at FROM review_sessions WHERE slot_id::text=$1 AND completed_at IS NOT NULL AND document_version_id<>$2 ORDER BY completed_at DESC,id DESC LIMIT 1", slot_id, body.document_version_id)
            if not prior:
                raise HTTPException(409, "没有上一版人工复核反馈，不能把文件版本或重新分析当作整改轮次")
            result = json_value(prior["review_result"], {})
            counts = result.get("issue_counts") or {}
            manual = (result.get("coverage") or {}).get("manual_decisions") or {}
            confirmed = len(result["confirmed_findings"]) if "confirmed_findings" in result else int(counts.get("confirmed", 0)) + int(counts.get("in_package", 0)) + int(manual.get("verified_issue", 0))
            if not confirmed:
                raise HTTPException(409, "上一版没有确认问题，不登记为整改提交")
            versions = await conn.fetch("SELECT id,file_hash,created_at FROM fiscal_document_versions WHERE id=ANY($1::int[])", [body.document_version_id, prior["document_version_id"]])
            by_id = {r["id"]: r for r in versions}
            current = by_id.get(body.document_version_id)
            previous = by_id.get(prior["document_version_id"])
            if not current or not previous or current["file_hash"] == previous["file_hash"] or current["created_at"] < prior["completed_at"]:
                raise HTTPException(409, "整改必须是在原复核反馈后提交的不同内容文件")
            existing = await conn.fetchrow("SELECT id FROM supervision_rounds WHERE slot_id::text=$1 AND document_version_id=$2", slot_id, body.document_version_id)
            if existing:
                return {"id": str(existing["id"]), "replayed": True}
            round_number = await conn.fetchval("SELECT COALESCE(MAX(round_number),0)+1 FROM supervision_rounds WHERE slot_id::text=$1", slot_id)
            result = await conn.fetchrow("INSERT INTO supervision_rounds(slot_id,document_version_id,feedback_session_id,round_number,submitted_by,note) VALUES($1::uuid,$2,$3,$4,$5,$6) RETURNING id", slot_id, body.document_version_id, prior["id"], round_number, user["username"], body.note)
            return {"id": str(result["id"]), "round_number": round_number}
    result = await with_connection(write)
    append_audit_event(action="supervision.round.register", actor=user["username"], result="success", resource_type="material_slot", resource_id=slot_id)
    return {"ok": True, "data": result}


class IssueVerification(BaseModel):
    previous_issue_id: str
    resolution: Literal["fixed", "remains"]
    current_issue_id: str | None = None


class VerifyRequest(NotedRequest):
    review_session_id: str
    comparisons: list[IssueVerification] = Field(max_length=5000)
    note: str = Field(min_length=8, max_length=4000)


def verify_comparisons(previous: list[dict], current: list[dict], comparisons: list[IssueVerification]) -> dict:
    old_ids = {i["issue_id"] for i in previous}
    current_ids = {i["issue_id"] for i in current}
    if len(comparisons) != len(old_ids) or {i.previous_issue_id for i in comparisons} != old_ids:
        raise HTTPException(422, "请逐条核验全部上一轮问题，不能以消失或数量下降代替已修复结论")
    linked = [i.current_issue_id for i in comparisons if i.resolution == "remains"]
    if any(i not in current_ids for i in linked) or len(set(linked)) != len(linked):
        raise HTTPException(422, "仍存在的问题必须关联到本轮真实确认问题，且不得重复关联")
    if any(i.current_issue_id for i in comparisons if i.resolution == "fixed"):
        raise HTTPException(422, "已修复项不能同时关联仍存在的问题")
    return {"fixed": sum(i.resolution == "fixed" for i in comparisons), "remaining": len(linked),
            "new": len(current_ids - set(linked)), "comparisons": [i.model_dump() for i in comparisons]}


@router.post("/api/supervision/materials/{slot_id}/rounds/{round_id}/verify")
async def verify_round(request: Request, slot_id: str, round_id: str, body: VerifyRequest):
    user, access, _, _ = context(request)
    async def write(conn):
        async with conn.transaction():
            slot = await conn.fetchrow("SELECT * FROM material_slots WHERE id::text=$1 FOR UPDATE", slot_id)
            material_access.require_slot_access(access, dict(slot) if slot else None, slot_id)
            detail = await material_detail(conn, slot_id, access.visible_org_ids)
            round_row = await conn.fetchrow("SELECT * FROM supervision_rounds WHERE id::text=$1 AND slot_id::text=$2 FOR UPDATE", round_id, slot_id)
            if not round_row:
                raise HTTPException(404, "整改记录不存在")
            current = next((r for r in detail["reviews"] if r["session_id"] == body.review_session_id
                and r["document_version_id"] == slot["current_document_version_id"] and r["basis_token"] == detail["material"]["basis_token"]), None)
            if detail["material"]["review_status"] != "completed" or not current or round_row["document_version_id"] != slot["current_document_version_id"]:
                raise HTTPException(409, "请先完成当前版本的人工复核，旧代际不能认定本轮已修复")
            if body.review_session_id != detail["material"]["review_session_id"]:
                raise HTTPException(409, "复核会话已变化，请刷新后核验最新结论")
            revision = await conn.fetchval("SELECT analysis_revision FROM analysis_jobs WHERE job_uuid=$1 FOR SHARE", detail["material"]["job_id"])
            if build_analysis_basis_token(detail["material"]["job_id"], revision) != current["basis_token"]:
                raise HTTPException(409, "分析代际已变化，不能签署旧结果的修复结论")
            previous = next((r for r in detail["reviews"] if r["session_id"] == str(round_row["feedback_session_id"])), None)
            if not previous or not previous["snapshot_complete"] or not current["snapshot_complete"]:
                raise HTTPException(409, "历史问题身份快照不完整，只保留历史计数，不倒推已修复问题")
            result = verify_comparisons(previous["findings"], current["findings"], body.comparisons)
            result.update({"note": body.note, "basis_token": current["basis_token"], "review_session_id": current["session_id"]})
            if round_row.get("verification_result") is not None:
                # 已签署的复验结论保留，不能靠刷新或重复提交静默改写。
                if json_value(round_row["verification_result"], {}) != result:
                    raise HTTPException(409, "本轮复验结论已记录，请保留原审计记录")
                return result
            await conn.execute("UPDATE supervision_rounds SET verification_session_id=$2::uuid,verification_result=$3::jsonb,verified_by=$4,verified_at=NOW() WHERE id::text=$1", round_id, body.review_session_id, json.dumps(result, ensure_ascii=False), user["username"])
            return result
    result = await with_connection(write)
    append_audit_event(action="supervision.round.verify", actor=user["username"], result="success", resource_type="material_slot", resource_id=slot_id)
    return {"ok": True, "data": result}
