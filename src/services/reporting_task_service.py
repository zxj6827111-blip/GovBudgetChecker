"""年度报送任务：版本化名册发布；所有页面读取同一份材料及复核事实。"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any
import uuid

from fastapi import HTTPException

from src.services.reporting_task_model import (candidate_roster, catalog_token, classify_item, default_components,
    digest, filter_items, jurisdiction, parse_due, region_members, roster_errors, row_identity, summarize, validate_components)
from src.services.material_slot_resolver import decide_slot_allocation
from src.services.material_slot_service import MaterialSlotService
from src.services.supervision_metrics import json_value, paginate
from src.services import supervision_query


def unpack(record: Any) -> dict:
    result = dict(record)
    for key in ("components", "draft_roster", "roster_snapshot", "slot_ids", "active_components"):
        if key in result:
            result[key] = json_value(result[key], [])
    return result


def public_task(task: dict) -> dict:
    public = {k: v for k, v in task.items() if k not in {"draft_roster", "draft_catalog_token"}}
    public["draft_components"] = task["components"]
    public["components"] = task.get("active_components") or task["components"]
    return public


class ReportingTaskService:
    def __init__(self, conn: Any, catalog: list[dict]):
        self.conn, self.catalog = conn, catalog

    async def get(self, task_id: str, *, lock: bool = False) -> dict:
        try:
            key = uuid.UUID(task_id)
        except ValueError as exc:
            raise HTTPException(422, "年度任务ID不合法") from exc
        row = await self.conn.fetchrow("SELECT * FROM reporting_tasks WHERE id=$1" + (" FOR UPDATE" if lock else ""), key)
        if row is None:
            raise HTTPException(404, "年度任务不存在")
        return unpack(row)

    async def create(self, region_id: str, year: int, actor: str, components: list[dict] | None = None) -> dict:
        region = next((r for r in self.catalog if str(r["id"]) == region_id and r["level"] in {"city", "district"}), None)
        if region is None:
            raise HTTPException(422, "请选择有效的市本级或区县")
        parts = validate_components(components or default_components(year))
        row = await self.conn.fetchrow("""INSERT INTO reporting_tasks(reporting_year,jurisdiction_org_id,components,created_by)
            VALUES($1,$2,$3::jsonb,$4) ON CONFLICT(reporting_year,jurisdiction_org_id) DO NOTHING RETURNING *""",
            year, region_id, json.dumps(parts), actor)
        if row is None:
            row = await self.conn.fetchrow("SELECT * FROM reporting_tasks WHERE reporting_year=$1 AND jurisdiction_org_id=$2", year, region_id)
        return public_task(unpack(row))

    async def list_regions(self, year: int, visible_ids: list[str] | None, containers: set[str]) -> list[dict]:
        tasks = {r["jurisdiction_org_id"]: public_task(unpack(r)) for r in await self.conn.fetch("SELECT * FROM reporting_tasks WHERE reporting_year=$1", year)}
        regions = []
        for org in self.catalog:
            if org["level"] not in {"city", "district"}:
                continue
            if visible_ids is not None and str(org["id"]) not in visible_ids and not any(str(m["id"]) in visible_ids for m in region_members(str(org["id"]), self.catalog)):
                continue
            regions.append({"id": str(org["id"]), "name": org["name"] + ("本级" if org["level"] == "city" else ""),
                "level": org["level"], "task": tasks.get(str(org["id"])), "partial_scope": visible_ids is not None and str(org["id"]) not in visible_ids})
        return sorted(regions, key=lambda r: (r["level"] != "city", r["name"], r["id"]))

    @staticmethod
    def check_revision(task: dict, revision: int) -> None:
        if task["state"] == "archived":
            raise HTTPException(409, "任务已归档，不能修改")
        if task["draft_revision"] != revision:
            raise HTTPException(409, "草稿已经变化，请刷新后重试")

    async def update(self, task_id: str, revision: int, actor: str, *, components: list[dict] | None = None,
                     archive: bool = False) -> dict:
        async with self.conn.transaction():
            task = await self.get(task_id, lock=True)
            self.check_revision(task, revision)
            if archive:
                await self.conn.execute("UPDATE reporting_tasks SET state='archived',draft_revision=draft_revision+1,updated_at=NOW() WHERE id=$1", task["id"])
            else:
                parts = validate_components(components or task["components"])
                await self.conn.execute("""UPDATE reporting_tasks SET components=$2::jsonb,draft_roster='[]'::jsonb,
                    draft_revision=draft_revision+1,draft_catalog_token='',updated_at=NOW() WHERE id=$1""", task["id"], json.dumps(parts))
        return public_task(await self.get(task_id))

    async def generate(self, task_id: str, revision: int) -> dict:
        async with self.conn.transaction():
            task = await self.get(task_id, lock=True)
            self.check_revision(task, revision)
            rows = candidate_roster(task["jurisdiction_org_id"], self.catalog, task["components"], task["draft_roster"])
            await self.conn.execute("""UPDATE reporting_tasks SET draft_roster=$2::jsonb,draft_revision=draft_revision+1,
                draft_catalog_token=$3,updated_at=NOW() WHERE id=$1""", task["id"], json.dumps(rows, ensure_ascii=False), catalog_token(task["jurisdiction_org_id"], self.catalog))
        return await self.roster(task_id)

    async def roster(self, task_id: str, visible_ids: list[str] | None = None, *, include_draft: bool = True) -> dict:
        task = await self.get(task_id)
        rows = task["draft_roster"]
        if not include_draft:
            published = await self.conn.fetch("SELECT roster_snapshot FROM supervision_baselines WHERE task_id=$1 AND roster_revision=$2 ORDER BY report_kind", task["id"], task["active_revision"])
            rows = [item for baseline in published for item in json_value(baseline["roster_snapshot"], [])]
        if visible_ids is not None:
            rows = [r for r in rows if r["subject_org_id"] in visible_ids]
        versions = await self.conn.fetch("""SELECT roster_revision,fiscal_year,report_kind,confirmed_by,note,created_at
            FROM supervision_baselines WHERE task_id=$1 ORDER BY roster_revision DESC,report_kind""", task["id"])
        return {"task": public_task(task), "items": rows, "versions": [dict(v) for v in versions],
                "catalog_changed": bool(task["draft_catalog_token" if include_draft else "active_catalog_token"]) and task["draft_catalog_token" if include_draft else "active_catalog_token"] != catalog_token(task["jurisdiction_org_id"], self.catalog)}

    async def edit(self, task_id: str, revision: int, updates: list[dict]) -> dict:
        async with self.conn.transaction():
            task = await self.get(task_id, lock=True)
            self.check_revision(task, revision)
            by_key = {r["key"]: r for r in task["draft_roster"]}
            if len({r["key"] for r in updates}) != len(updates) or any(r["key"] not in by_key for r in updates):
                raise HTTPException(422, "更新包含重复或不存在的候选行")
            for change in updates:
                row = by_key[change["key"]]
                for key in ("decision", "reason", "due_at"):
                    if key in change:
                        row[key] = change[key]
            await self.conn.execute("UPDATE reporting_tasks SET draft_roster=$2::jsonb,draft_revision=draft_revision+1,updated_at=NOW() WHERE id=$1",
                task["id"], json.dumps(list(by_key.values()), ensure_ascii=False))
        return await self.roster(task_id)

    async def import_rows(self, task_id: str, revision: int, csv_rows: list[dict]) -> dict:
        task = await self.get(task_id)
        changes = []
        by_key = {r["key"]: r for r in task["draft_roster"]}
        for row in csv_rows:
            identity = row_identity(row)
            if identity.slot_key not in by_key:
                raise HTTPException(422, "CSV含当前任务草稿之外的主体或年度，请先补全组织目录并刷新草稿")
            changes.append({"key": identity.slot_key, "decision": "required", "due_at": row["due_at"], "reason": "CSV导入草稿"})
        return await self.edit(task_id, revision, changes)

    async def preview(self, task: dict) -> dict:
        rows = task["draft_roster"]
        errors = roster_errors(rows, self.catalog, task["jurisdiction_org_id"], task["components"])
        if task["draft_catalog_token"] != catalog_token(task["jurisdiction_org_id"], self.catalog):
            errors.append({"key": "", "reason": "组织目录已变化，请刷新草稿并确认新增、变更的候选"})
        required = [r for r in rows if r["decision"] == "required"]
        records = [dict(r) for r in await self.conn.fetch("SELECT * FROM material_slots WHERE slot_key=ANY($1::text[]) ORDER BY slot_key", [r["key"] for r in required])]
        existing = {r["slot_key"]: r for r in records}
        for r in required:
            slot = existing.get(r["key"])
            if slot and (slot["caliber"] != r["caliber"] or slot.get("caliber_conflict_candidate") or slot.get("mapping_key") or
                         slot.get("jurisdiction_org_id") != r["jurisdiction_org_id"] or slot.get("department_org_id") != r["department_org_id"] or
                         slot["applicability_status"] != "applicable" or
                         (slot.get("due_at") is not None and slot["due_at"] != parse_due(r.get("due_at")))):
                errors.append({"key": r["key"], "reason": "既有材料口径、适用性或截止时间冲突，请先核对，发布不会覆盖既有决定"})
        token = digest({"task": str(task["id"]), "revision": task["draft_revision"], "roster": rows,
                        "catalog": catalog_token(task["jurisdiction_org_id"], self.catalog),
                        "existing": [{k: r.get(k) for k in ("id", "slot_key", "caliber", "caliber_conflict_candidate", "applicability_status", "due_at")} for r in records]})
        return {"valid": not errors, "errors": errors, "preview_token": token,
                "required": len(required), "not_applicable": sum(r["decision"] == "not_applicable" for r in rows),
                "excluded": sum(r["decision"] == "excluded" for r in rows),
                "due_unknown": sum(not r.get("due_at") for r in required), "draft_revision": task["draft_revision"]}

    async def publish(self, task_id: str, revision: int, token: str, note: str, actor: str) -> dict:
        async with self.conn.transaction():
            task = await self.get(task_id, lock=True)
            self.check_revision(task, revision)
            # 与上传共用身份锁序；多项发布先取全部身份锁，再取槽位行锁。
            service = MaterialSlotService(self.conn)
            required = sorted([r for r in task["draft_roster"] if r["decision"] == "required"], key=lambda r: r["key"])
            for row in required:
                await service._lock_slot_identity(row["key"])
            await self.conn.fetch("SELECT id FROM material_slots WHERE slot_key=ANY($1::text[]) ORDER BY slot_key FOR UPDATE", [r["key"] for r in required])
            preview = await self.preview(task)
            if not preview["valid"]:
                raise HTTPException(409, {"message": "名册尚未通过校验", "errors": preview["errors"]})
            if preview["preview_token"] != token:
                raise HTTPException(409, "草稿、目录或材料身份已变化，请重新预览")
            snapshots = [dict(r) for r in task["draft_roster"]]
            for row in snapshots:
                row["slot_id"] = None
                if row["decision"] != "required":
                    continue
                decision = decide_slot_allocation(metadata={"organization_id": row["subject_org_id"], "report_year": row["fiscal_year"], "report_kind": row["report_kind"]}, org_records=self.catalog)
                if not decision.ok or decision.identity is None or decision.identity.slot_key != row["key"]:
                    raise HTTPException(409, "主体身份变化，请刷新草稿")
                existed = await self.conn.fetchval("SELECT id FROM material_slots WHERE slot_key=$1", row["key"])
                await service._ensure_slot_row(identity=decision.identity, decision=decision, caliber=row["caliber"], due_at=parse_due(row.get("due_at")), expected_source_url=None)
                current = await self.conn.fetchrow("SELECT id FROM material_slots WHERE slot_key=$1", row["key"])
                row["slot_id"] = str(current["id"])
                # 发布只初始化新槽位，已有分析和复核状态、时间戳原样保留。
                if existed is None:
                    await service.refresh_status(current["id"])
            for component in task["components"]:
                part = [r for r in snapshots if r["report_kind"] == component["report_kind"]]
                await self.conn.execute("""INSERT INTO supervision_baselines(scope_org_id,fiscal_year,report_kind,slot_ids,
                    confirmed_by,note,task_id,roster_revision,roster_snapshot) VALUES($1,$2,$3,$4::jsonb,$5,$6,$7,$8,$9::jsonb)""",
                    task["jurisdiction_org_id"], component["fiscal_year"], component["report_kind"], json.dumps([r["slot_id"] for r in part if r["decision"] == "required"]),
                    actor, note, task["id"], revision, json.dumps(part, ensure_ascii=False))
            await self.conn.execute("UPDATE reporting_tasks SET active_revision=$2,active_catalog_token=$3,active_components=components,state='active',draft_revision=draft_revision+1,updated_at=NOW() WHERE id=$1", task["id"], revision, task["draft_catalog_token"])
        return {"task": public_task(await self.get(task_id)), "published_revision": revision, "required": len(required)}

    async def statistics(self, task_id: str, *, visible_ids: list[str] | None, component: str = "all", status: str = "all",
                         keyword: str = "", department_id: str = "", subject_id: str = "", view: str = "subjects", page: int = 1, page_size: int = 20, export: bool = False) -> dict:
        task = await self.get(task_id)
        baselines = [unpack(r) for r in await self.conn.fetch("SELECT * FROM supervision_baselines WHERE task_id=$1 AND roster_revision=$2 ORDER BY report_kind", task["id"], task["active_revision"])] if task["active_revision"] is not None else []
        parts = task.get("active_components") or task["components"]
        published = len(baselines) == len(parts)
        # 默认口径由区域组织目录直接给出，不以草稿确认或已有上传作为应交分母。
        # 已发布的历史名单仍按其不可变快照读取；目录模式只构造查询行，不写槽位或发布名单。
        directory_based = task["active_revision"] is None and any(
            str(o["id"]) == task["jurisdiction_org_id"] and o["level"] in {"city", "district"} for o in self.catalog)
        ready = published or directory_based
        all_snapshots = ([{**r, "decision": "required"} for r in candidate_roster(task["jurisdiction_org_id"], self.catalog, parts)]
                         if directory_based else [r for b in baselines for r in b["roster_snapshot"]])
        snapshots = [r for r in all_snapshots if (visible_ids is None or r["subject_org_id"] in visible_ids)]
        members = {str(o["id"]) for o in region_members(task["jurisdiction_org_id"], self.catalog)}
        ids = members | {r["subject_org_id"] for r in snapshots}
        if visible_ids is not None:
            ids &= set(visible_ids)
        rows = []
        for part in parts:
            rows += await supervision_query.load_rows(self.conn, visible_org_ids=visible_ids, scope_ids=sorted(ids), fiscal_year=part["fiscal_year"], report_kind=part["report_kind"])
        rows = [r for r in rows if not r.get("mapping_key") and not r.get("caliber_conflict_candidate")]
        for row in rows:
            if row.get("valid_document_version_id") is None:
                row["current_document_version_id"] = None
                row["id"] = None
        current_items = await supervision_query.build_items(self.conn, rows)
        by_slot = {i["slot_id"]: i for i in current_items}
        if directory_based and by_slot:
            facts = await self.conn.fetch("""SELECT id::text AS slot_id,subject_org_id,subject_kind,material_scope,
                report_kind,fiscal_year,caliber,jurisdiction_org_id,due_at FROM material_slots WHERE id::text=ANY($1::text[])""", list(by_slot))
            by_identity: dict[str, list] = {}
            for fact in facts:
                if fact["jurisdiction_org_id"] == task["jurisdiction_org_id"]:
                    by_identity.setdefault(row_identity(dict(fact)).slot_key, []).append(fact)
            for snapshot in snapshots:
                matches = by_identity.get(snapshot["key"], [])
                # 以明确的主体、材料范围、年度和文种匹配；历史 caliber=unknown 不覆盖已确认的材料范围。
                if len(matches) == 1:
                    snapshot["slot_id"] = matches[0]["slot_id"]
                    due = matches[0]["due_at"]
                    snapshot["due_at"] = due.isoformat() if due is not None else None
        now = datetime.now(timezone.utc)
        classified = []
        missing_references = 0
        for snapshot in snapshots:
            current = by_slot.get(snapshot.get("slot_id"))
            if current and (current["organization_id"] != snapshot["subject_org_id"] or current["fiscal_year"] != snapshot["fiscal_year"] or current["report_kind"] != snapshot["report_kind"]):
                current = None
            if published and snapshot["decision"] == "required" and current is None:
                missing_references += 1
            item = classify_item(snapshot, current, now)
            if directory_based:
                item.update(expected=1, missing=int(not item["uploaded"]))
                if not item["uploaded"] and not item.get("due_at"):
                    item["overdue"] = None
            classified.append(item)
        chosen = [r for r in classified if component == "all" or r["report_kind"] == component]
        required_ids = {r["slot_id"] for r in chosen if r["decision"] == "required"}
        observed = [i for i in current_items if (component == "all" or i["report_kind"] == component) and i["uploaded"]]
        extras = [i for i in observed if i["slot_id"] not in required_ids]
        # 未知归属只提供授权范围内的记录；管理员才能看全局未归区划数量。
        from src.services.material_ledger_query_service import MaterialSlotFilters
        unknown = []
        for part in parts:
            if component != "all" and component != part["report_kind"]:
                continue
            where, params = MaterialSlotFilters(visible_org_ids=visible_ids, fiscal_year=part["fiscal_year"], report_kind=part["report_kind"]).where()
            unknown += [dict(r) for r in await self.conn.fetch(f"SELECT id::text AS slot_id,subject_org_id,subject_org_name,subject_kind,department_org_id,jurisdiction_org_id,fiscal_year,report_kind,mapping_key,caliber_conflict_candidate FROM material_slots WHERE {where} AND (jurisdiction_org_id IS NULL OR department_org_id IS NULL OR mapping_key<>'' OR COALESCE(caliber_conflict_candidate,'')<>'')", *params)]
        def scope_summary(items: list[dict], observed_items: list[dict]) -> dict:
            result = summarize(items, ready, observed_items=observed_items)
            if directory_based:
                result["department_count"] = len({r["organization_id"] for r in items if r["subject_kind"] == "department"})
            # 全部未交项都没有截止时间时，逾期总量仍未知，不能用零冒充已确认没有逾期。
            if directory_based and result["missing"] and result["due_unknown_missing"] == result["missing"]:
                result["overdue_missing"] = None
            return result
        summary = scope_summary(chosen, observed)
        summary.update({"observed_uploaded": len(observed), "extra_uploaded": len(extras) if ready else None, "unassigned_materials": len(unknown), "missing_references": missing_references})
        # 目录应交行包含尚未建立槽位的主体，未上传主体也必须进入部门和未交明细。
        base = [r for r in chosen if r["decision"] == "required"] if ready else observed
        group_rows = []
        groups: dict[str, list] = {}
        for r in base:
            groups.setdefault(r.get("department_id") or r["organization_id"], []).append(r)
        for key, group in groups.items():
            group_rows.append({"id": key, "name": group[0].get("department_name") or group[0]["organization_name"], **scope_summary(group, group)})
        selected = filter_items(base, status=status, keyword=keyword, department_id=department_id, subject_id=subject_id, baseline_ready=ready)
        if view == "departments":
            selected_ids = {r.get("department_id") or r["organization_id"] for r in selected}
            display = [r for r in group_rows if r["id"] in selected_ids]
        elif view in {"issues", "leads"}:
            display = [{**f, "slot_id": r["slot_id"], "organization_id": r["organization_id"], "document_version_id": r.get("document_version_id"), "job_id": r.get("job_id"), "organization_name": r["organization_name"], "department_name": r.get("department_name"), "report_kind": r["report_kind"], "fiscal_year": r["fiscal_year"]} for r in selected for f in (r.get("_pending_findings", []) if view == "leads" else r["findings"])]
        elif view == "extras":
            display = filter_items(extras, status=status, keyword=keyword, department_id=department_id, subject_id=subject_id, baseline_ready=ready)
        elif view == "remediation":
            display = [{k: v for k, v in r.items() if k != "findings" and not k.startswith("_")} for r in selected if r.get("rounds_count") or r.get("history_incomplete")]
        else:
            display = [{k: v for k, v in r.items() if k != "findings" and not k.startswith("_")} for r in selected]
        region = next((r for r in self.catalog if str(r["id"]) == task["jurisdiction_org_id"]), {})
        return {"task": public_task(task), "region_name": str(region.get("name") or task["jurisdiction_org_id"]) + ("本级" if region.get("level") == "city" else ""),
            "summary": summary, "components": [{**p, "summary": scope_summary([r for r in classified if r["report_kind"] == p["report_kind"]],
                [r for r in current_items if r["uploaded"] and r["report_kind"] == p["report_kind"]])} for p in parts],
            "departments": group_rows, "list": paginate(display, page, page_size), "export_rows": display if export else [],
            "meta": {"baseline_ready": ready, "expected_materials_ready": ready, "roster_revision": task["active_revision"],
                "data_basis": "organization_catalog" if directory_based else "confirmed_expected_list" if ready else "existing_slots_only",
                "roster_published": published, "generated_at": now.isoformat(),
                "scope_basis": "jurisdiction_own", "partial_scope": visible_ids is not None and (task["jurisdiction_org_id"] not in visible_ids or len(snapshots) != len(all_snapshots)),
                "baseline_by": baselines[0]["confirmed_by"] if baselines else None, "baseline_note": baselines[0]["note"] if baselines else None,
                "baseline_at": baselines[0]["created_at"] if baselines else None,
                "catalog_changed": published and task["active_catalog_token"] != catalog_token(task["jurisdiction_org_id"], self.catalog)}}
