"""区域年度报送：名册写入仅管理员可用，读接口始终按组织权限收缩。"""
from __future__ import annotations

import csv
import io
import os
from typing import Literal
from contextlib import asynccontextmanager
from asyncpg.exceptions import SerializationError, DeadlockDetectedError

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from api import material_access, runtime
from api.auth_utils import require_admin, require_login
from src.services.audit_log import append_audit_event
from src.services.reporting_task_service import ReportingTaskService

router = APIRouter(prefix="/api/reporting-tasks")


def enabled() -> bool:
    return os.getenv("GOVBUDGET_REPORTING_TASKS_ENABLED", "false").lower() not in {"0", "false", "off", "no"}


def context(request: Request, *, write: bool = False):
    if not enabled():
        raise HTTPException(503, "年度报送视图已关闭，可使用历史材料查询")
    _, _, user = require_admin(request) if write else require_login(request)
    if write and user.get("is_read_only"):
        raise HTTPException(403, "主管账号只读")
    scope = material_access.build_access_scope(user)
    if not scope.unrestricted and not scope.has_any_visible_org:
        raise HTTPException(403, "当前账号未配置组织授权")
    catalog = [{"id": str(r.id), "name": r.name, "level": r.level, "parent_id": r.parent_id, "code": getattr(r, "code", None)} for r in runtime.require_org_storage().get_all()]
    return user, scope, catalog


@asynccontextmanager
async def query_snapshot(conn, write: bool):
    try:
        async with conn.transaction(isolation="repeatable_read", readonly=not write):
            yield
    except (SerializationError, DeadlockDetectedError) as exc:
        raise HTTPException(409, "名单或材料被并发修改，请刷新并重新预览") from exc


async def invoke(request: Request, task_id: str, callback, *, write: bool = False):
    user, scope, catalog = context(request, write=write)
    async def run(conn):
        # 发布、修复有自己的写事务；所有统计/导出在同一只读快照内取数。
        async with query_snapshot(conn, write):
            service = ReportingTaskService(conn, catalog)
            task = await service.get(task_id)
            material_access.require_container_access(scope, task["jurisdiction_org_id"], "无权查看该地区年度任务")
            if scope.visible_org_ids is not None:
                from src.services.reporting_task_model import region_members
                members = {str(m["id"]) for m in region_members(task["jurisdiction_org_id"], catalog)}
                if task["jurisdiction_org_id"] not in scope.visible_org_ids and not members.intersection(scope.visible_org_ids):
                    raise HTTPException(403, "未获该地区本级部门或单位授权")
            try:
                return await callback(service, task, user, scope)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
    return await material_access.with_connection(run)


def event(action: str, actor: str, resource: str) -> None:
    append_audit_event(action="reporting_task." + action, actor=actor, result="success", resource_type="reporting_task", resource_id=resource)


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Component(StrictBody):
    fiscal_year: int = Field(ge=2000, le=2099)
    report_kind: Literal["budget", "final"]


class CreateTask(StrictBody):
    jurisdiction_org_id: str = Field(min_length=1, max_length=100)
    reporting_year: int = Field(ge=2001, le=2099)
    components: list[Component] | None = Field(default=None, max_length=2)


class RevisionBody(StrictBody):
    revision: int = Field(ge=0)


class TaskUpdate(RevisionBody):
    components: list[Component] | None = Field(default=None, max_length=2)
    archive: bool = False


class RowChange(StrictBody):
    key: str = Field(min_length=1, max_length=128)
    decision: Literal["pending", "required", "not_applicable", "excluded"] | None = None
    reason: str | None = Field(default=None, max_length=2000)
    due_at: str | None = Field(default=None, max_length=60)


class EditRoster(RevisionBody):
    updates: list[RowChange] = Field(max_length=20000)


class PublishRoster(RevisionBody):
    preview_token: str = Field(min_length=64, max_length=64)
    note: str = Field(min_length=4, max_length=2000)
    completeness_confirmed: Literal[True]


class CsvDraft(RevisionBody):
    csv_text: str = Field(max_length=1_000_000)


class IdentityPreview(StrictBody):
    old_id: str = Field(min_length=1, max_length=100)
    new_id: str = Field(min_length=1, max_length=100)
    slot_ids: list[str] = Field(min_length=1, max_length=500)


class IdentityApply(IdentityPreview):
    preview_token: str = Field(min_length=64, max_length=64)
    note: str = Field(min_length=8, max_length=2000)
    same_subject_confirmed: Literal[True]


@router.get("/capabilities")
async def capabilities(request: Request):
    _, _, user = require_login(request)
    return {"ok": True, "enabled": enabled(), "can_manage": bool(user.get("is_admin")) and not user.get("is_read_only")}


@router.get("")
async def list_tasks(request: Request, reporting_year: int = Query(ge=2001, le=2099)):
    user, scope, catalog = context(request)
    async def read(conn):
        rows = await ReportingTaskService(conn, catalog).list_regions(reporting_year, scope.visible_org_ids, set(scope.container_org_ids or []))
        return {"ok": True, "data": {"regions": rows, "reporting_year": reporting_year, "can_manage": bool(user.get("is_admin")) and not user.get("is_read_only")}}
    return await material_access.with_connection(read)


@router.post("")
async def create_task(request: Request, body: CreateTask):
    user, scope, catalog = context(request, write=True)
    async def write(conn):
        try:
            return await ReportingTaskService(conn, catalog).create(body.jurisdiction_org_id, body.reporting_year, user["username"], [p.model_dump() for p in body.components] if body.components else None)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    task = await material_access.with_connection(write)
    event("create", user["username"], str(task["id"]))
    return {"ok": True, "data": task}


@router.patch("/{task_id}")
async def update_task(request: Request, task_id: str, body: TaskUpdate):
    result = await invoke(request, task_id, lambda service, task, user, scope: service.update(task_id, body.revision, user["username"], components=[p.model_dump() for p in body.components] if body.components is not None else None, archive=body.archive), write=True)
    event("configure", context(request, write=True)[0]["username"], task_id)
    return {"ok": True, "data": result}


@router.get("/{task_id}/roster")
async def read_roster(request: Request, task_id: str):
    return {"ok": True, "data": await invoke(request, task_id, lambda service, task, user, scope: service.roster(task_id, scope.visible_org_ids, include_draft=bool(user.get("is_admin")) and not user.get("is_read_only")))}


@router.post("/{task_id}/roster/generate")
async def generate_roster(request: Request, task_id: str, body: RevisionBody):
    data = await invoke(request, task_id, lambda service, task, user, scope: service.generate(task_id, body.revision), write=True)
    event("generate", context(request, write=True)[0]["username"], task_id)
    return {"ok": True, "data": data}


@router.patch("/{task_id}/roster")
async def edit_roster(request: Request, task_id: str, body: EditRoster):
    data = await invoke(request, task_id, lambda service, task, user, scope: service.edit(task_id, body.revision, [r.model_dump(exclude_unset=True) for r in body.updates]), write=True)
    event("edit_roster", context(request, write=True)[0]["username"], task_id)
    return {"ok": True, "data": data}


@router.post("/{task_id}/roster/import")
async def import_roster(request: Request, task_id: str, body: CsvDraft):
    from src.services.expected_material_import import preview_csv
    async def write(service, task, user, scope):
        preview = preview_csv(body.csv_text, service.catalog, scope.visible_org_ids, allow_unknown_due=True)
        if not preview["valid"]:
            raise HTTPException(422, preview)
        return await service.import_rows(task_id, body.revision, preview["rows"])
    data = await invoke(request, task_id, write, write=True)
    event("import_draft", context(request, write=True)[0]["username"], task_id)
    return {"ok": True, "data": data}


@router.post("/{task_id}/roster/preview")
async def preview_roster(request: Request, task_id: str, body: RevisionBody):
    async def preview(service, task, user, scope):
        service.check_revision(task, body.revision)
        return await service.preview(task)
    return {"ok": True, "data": await invoke(request, task_id, preview, write=True)}


@router.post("/{task_id}/roster/publish")
async def publish_roster(request: Request, task_id: str, body: PublishRoster):
    data = await invoke(request, task_id, lambda service, task, user, scope: service.publish(task_id, body.revision, body.preview_token, body.note, user["username"]), write=True)
    event("publish", context(request, write=True)[0]["username"], task_id)
    return {"ok": True, "data": data}


@router.get("/{task_id}/identity-cases")
async def identity_cases(request: Request, task_id: str, include_history: bool = False):
    from src.services.reporting_identity import cases
    if context(request)[0].get("is_read_only"):
        raise HTTPException(403, "主管视图仅显示归属待核查数量")
    if include_history:
        context(request, write=True)
    return {"ok": True, "data": await invoke(request, task_id, lambda service, task, user, scope: cases(service.conn, task, service.catalog, include_history=include_history, visible_ids=scope.visible_org_ids))}


@router.post("/{task_id}/identity-preview")
async def identity_preview(request: Request, task_id: str, body: IdentityPreview):
    from src.services.reporting_identity import preview
    return {"ok": True, "data": await invoke(request, task_id, lambda service, task, user, scope: preview(service.conn, task, service.catalog, body.old_id, body.new_id, body.slot_ids), write=True)}


@router.post("/{task_id}/identity-apply")
async def identity_apply(request: Request, task_id: str, body: IdentityApply):
    from src.services.reporting_identity import apply
    from src.services.org_storage import DATA_DIR, LINKS_FILE
    user, scope, catalog = context(request, write=True)
    async def write(conn):
        task = await ReportingTaskService(conn, catalog).get(task_id)
        material_access.require_container_access(scope, task["jurisdiction_org_id"], "无权修复此地区")
        if task["state"] == "archived":
            raise HTTPException(409, "任务已归档，请通过当前任务核查归属")
        try:
            return await apply(conn, task, catalog, old_id=body.old_id, new_id=body.new_id, slot_ids=body.slot_ids,
                token=body.preview_token, note=body.note, actor=user["username"], upload_root=runtime.UPLOAD_ROOT,
                links_file=LINKS_FILE, receipt_root=DATA_DIR / "identity-repairs")
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    result = await material_access.with_connection(write)
    event("identity_repair", user["username"], task_id)
    return {"ok": True, "data": result}


def csv_response(data: dict) -> Response:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["报送年度", data["task"]["reporting_year"], "地区", data["region_name"], "名册版本", data["meta"]["roster_revision"], "统计时间", data["meta"]["generated_at"]])
    writer.writerow(["口径", "授权范围内" if data["meta"]["partial_scope"] else "地区本级", "清单状态", "按区域组织目录计算" if data["meta"].get("data_basis") == "organization_catalog" else "已确认" if data["meta"]["baseline_ready"] else "未确认，应交与缺交未知"])
    writer.writerow(["筛选条件", str(data.get("filters", {}))])
    columns = [("organization_name", "主体"), ("department_name", "主管部门"), ("name", "部门"), ("fiscal_year", "财政年度"), ("report_kind", "文种"), ("uploaded", "已交" if data["meta"]["baseline_ready"] else "已上传"), ("expected", "应交"), ("missing", "未交"), ("overdue", "逾期未交"), ("confirmed_count", "确认问题"), ("confirmed", "部门确认问题"), ("pending_count", "待复核线索"), ("review_status", "复核状态"), ("title", "问题"), ("due_at", "截止时间")]
    writer.writerow([label for _, label in columns])
    for row in data["export_rows"]:
        values = []
        for key, _ in columns:
            value = row.get("overdue_missing") if key == "overdue" and "overdue_missing" in row else row.get(key)
            if isinstance(value, bool) and key in {"uploaded", "expected", "missing", "overdue"}:
                value = int(value)
            text = "未知" if value is None else str(value)
            values.append("'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text)
        writer.writerow(values)
    return Response("\ufeff" + out.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=regional-reporting.csv"})


@router.get("/{task_id}/statistics")
async def statistics(request: Request, task_id: str, component: Literal["all", "budget", "final"] = "all",
                     status: Literal["all", "empty", "partial", "complete", "missing", "overdue", "issues", "waiting", "received", "unit_complete", "analyzed", "reviewed"] = "all",
                     view: Literal["subjects", "departments", "issues", "leads", "extras", "remediation"] = "subjects", keyword: str = Query("", max_length=120),
                     department_id: str = "", subject_id: str = "", page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), format: Literal["json", "csv"] = "json"):
    data = await invoke(request, task_id, lambda service, task, user, scope: service.statistics(task_id, visible_ids=scope.visible_org_ids,
        component=component, status=status, keyword=keyword, department_id=department_id, subject_id=subject_id, view=view, page=page,
        page_size=page_size, export=format == "csv"))
    if format == "csv":
        data["filters"] = dict(component=component,status=status,view=view,keyword=keyword,department_id=department_id,subject_id=subject_id)
        return csv_response(data)
    data.pop("export_rows", None)
    return {"ok": True, "data": jsonable_encoder(data)}
