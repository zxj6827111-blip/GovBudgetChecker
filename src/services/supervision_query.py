"""主管端批量只读查询。按槽位与当前文件精确关联，不聚合上传目录。"""

from collections import defaultdict
from dataclasses import asdict
from typing import Any

from src.services import issue_workflow_store
from src.services.material_detail_query_service import (
    _RUN_SUMMARY_METADATA_PROJECTION, _RUN_VERSION_JOIN, build_coverage, select_current_run,
)
from src.services.material_ledger_query_service import MaterialSlotFilters
from src.services.review_context_query import analysis_from_run_row
from src.services.review_problem_set import collect_review_problems
from src.services.review_session_store import build_analysis_basis_token
from src.schemas.issues import normalize_job_status
from src.services.supervision_metrics import CONFIRMED, category, iso, json_value, normalize_finding, overdue, paginate, summarize


def _decision_payload_projection(column: str) -> str:
    """把 ``DECISION_PAYLOAD_PATHS`` 渲染成 jsonb 投影；缺失或类型不符的路径为 null。"""
    tree: dict = {}
    for path in issue_workflow_store.DECISION_PAYLOAD_PATHS:
        node = tree
        for key in path[:-1]:
            node = node.setdefault(key, {})
        node[path[-1]] = None

    def render(node: dict, prefix: str) -> str:
        parts = []
        for key, child in node.items():
            expression = f"{prefix} -> '{key}'"
            parts.append(f"'{key}', " + (render(child, expression) if isinstance(child, dict) else expression))
        return f"jsonb_build_object({', '.join(parts)})"

    return render(tree, column)


#: 人工判定只需要报告里的少数位置（见 ``DECISION_PAYLOAD_PATHS``），不传整份 raw_response。
_DECISION_PAYLOAD_PROJECTION = _decision_payload_projection("r.raw_response")


def confirmed_snapshot(state: Any) -> list[dict]:
    """在完成复核的事务中保存问题身份；新文件上传后仍可追溯旧结论。"""
    findings = [normalize_finding(asdict(problem)) for problem in state.problems
                if problem.is_formal and state.decisions.get(problem.issue_id) in CONFIRMED]
    for key, decision in state.obligation_decisions.items():
        if decision.get("decision") == "verified_issue":
            findings.append({"issue_id": "obligation:" + key, "title": decision.get("note") or "人工补核确认问题",
                "severity": "manual_review", "source": "obligation", "evidence_reference": decision.get("evidence_reference")})
    return findings


async def available_periods(conn: Any, *, visible_org_ids: list[str] | None,
                            scope_ids: list[str] | None) -> list[dict]:
    """只返回授权范围内已有台账的年度与文种；应交但未上传的槽位也算台账。"""
    where, params = MaterialSlotFilters(visible_org_ids=visible_org_ids).where()
    if scope_ids is not None:
        params.append(scope_ids)
        where += f" AND subject_org_id = ANY(${len(params)}::text[])"
    rows = await conn.fetch(f"""
        SELECT fiscal_year,report_kind,COUNT(*) AS material_count,
               COUNT(*) FILTER(WHERE current_document_version_id IS NOT NULL) AS uploaded
        FROM material_slots
        WHERE {where} AND fiscal_year BETWEEN 2000 AND 2099
              AND report_kind IN ('budget','final')
        GROUP BY fiscal_year,report_kind
        ORDER BY fiscal_year DESC,CASE WHEN report_kind='budget' THEN 0 ELSE 1 END
    """, *params)
    return [dict(row) for row in rows]


async def load_rows(conn: Any, *, visible_org_ids: list[str] | None, scope_ids: list[str] | None = None,
                    fiscal_year: int | None = None, report_kind: str = "all", slot_id: str | None = None) -> list[dict]:
    """材料与其当前文件上的全部运行，只含挑选当前运行所需的轻量列。

    分析结论、metadata 与 raw_response 由 ``build_items`` 只为当前运行补取；
    基线令牌与确认基线只用材料一级的字段，不必为它们传整份运行产物。
    """
    where, params = MaterialSlotFilters(visible_org_ids=visible_org_ids, fiscal_year=fiscal_year, report_kind=report_kind).where()
    if scope_ids is not None:
        params.append(scope_ids)
        where += f" AND subject_org_id = ANY(${len(params)}::text[])"
    if slot_id:
        params.append(slot_id)
        where += f" AND id::text = ${len(params)}"
    rows = await conn.fetch(f"""
        WITH scoped AS (SELECT * FROM material_slots WHERE {where})
        SELECT s.id::text AS slot_id, s.subject_org_id, s.subject_org_name, s.subject_kind,
          s.department_org_id, s.department_name, s.fiscal_year, s.report_kind, s.status AS slot_status,
          s.status_reason, s.applicability_status, s.due_at, s.current_document_version_id,
          s.mapping_key, s.caliber_conflict_candidate, s.updated_at AS slot_updated_at,
          v.original_filename, v.id AS valid_document_version_id, j.id, j.job_uuid, j.status, j.analysis_revision,
          j.created_at, j.updated_at, j.completed_at, j.started_at, r.id AS result_id
        FROM scoped s
        LEFT JOIN fiscal_document_versions v ON v.id=s.current_document_version_id AND v.slot_id=s.id
        LEFT JOIN analysis_jobs j ON {_RUN_VERSION_JOIN}
        LEFT JOIN analysis_results r ON r.job_id=j.id
    """, *params)
    return [dict(row) for row in rows]


async def _load_run_details(conn: Any, runs: list[dict], decided_job_ids: list[str]) -> dict[int, dict]:
    """当前运行的摘要字段、分析结论与判定所需的报告位置；一条语句取出，避免新旧混杂。

    raw_response 只为有人工判定记录的任务投影：没有判定记录时
    ``project_job_issue_decisions`` 不读报告内容，直接返回空。
    """
    if not runs:
        return {}
    rows = await conn.fetch(f"""
        SELECT j.id, j.status, j.analysis_revision, {_RUN_SUMMARY_METADATA_PROJECTION},
               r.id AS result_id, r.ai_findings, r.rule_findings, r.merged_result,
               CASE WHEN j.job_uuid = ANY($2::text[]) THEN {_DECISION_PAYLOAD_PROJECTION} END AS raw_response
        FROM analysis_jobs j LEFT JOIN analysis_results r ON r.job_id = j.id
        WHERE j.id = ANY($1::bigint[])
    """, [int(run["id"]) for run in runs], decided_job_ids)
    return {int(row["id"]): dict(row) for row in rows}


async def build_items(conn: Any, rows: list[dict]) -> list[dict]:
    from src.services.review_lifecycle_service import unhandled_coverage_obligations

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["slot_id"]].append(row)
    slot_ids = list(grouped)
    if not slot_ids:
        return []
    sessions = [dict(row) for row in await conn.fetch(
        "SELECT *, id::text AS session_id, slot_id::text AS slot_key FROM review_sessions WHERE slot_id::text=ANY($1::text[]) ORDER BY created_at DESC,id DESC", slot_ids)]
    rounds = [dict(row) for row in await conn.fetch(
        "SELECT *, id::text AS round_id, slot_id::text AS slot_key FROM supervision_rounds WHERE slot_id::text=ANY($1::text[]) ORDER BY round_number", slot_ids)]
    versions = await conn.fetch("SELECT slot_id::text AS slot_key, COUNT(*) AS total FROM fiscal_document_versions WHERE slot_id::text=ANY($1::text[]) GROUP BY slot_id", slot_ids)
    version_counts = {row["slot_key"]: int(row["total"]) for row in versions}
    obligations = [dict(row) for row in await conn.fetch(
        "SELECT review_session_id::text AS session_id,obligation_id,decision,note,evidence_reference FROM review_obligation_decisions WHERE review_session_id::text=ANY($1::text[])",
        [row["session_id"] for row in sessions])] if sessions else []
    # 一次读取文件工作流的恢复源；GET 不执行 legacy 迁移，不按材料重复读文件。
    with issue_workflow_store._state_lock():
        workflow = issue_workflow_store._read_state()
    # 每份材料只看当前运行：先挑出来，再一次补取它们的结论与判定所需的报告位置。
    current_runs = {slot: select_current_run([r for r in slot_rows if r.get("id") is not None])
                    for slot, slot_rows in grouped.items()}
    decided_job_ids = sorted({str(event.get("job_id") or "") for event in workflow.get("decision_events", [])} - {""})
    details = await _load_run_details(conn, [run for run in current_runs.values() if run], decided_job_ids)
    items = []
    for slot, slot_rows in grouped.items():
        row = slot_rows[0]
        run = current_runs[slot]
        # 两段之间运行被删除时按"未分析"处理，材料本身照常列出。
        detail = details.get(int(run["id"])) if run else None
        run = {**run, **detail} if run and detail else None
        analysis = analysis_from_run_row(run, int(row["current_document_version_id"])) if run else None
        basis = build_analysis_basis_token(analysis.job_uuid, analysis.analysis_revision) if analysis else None
        history = [s for s in sessions if s["slot_key"] == slot]
        matching = [s for s in history if s["document_version_id"] == row["current_document_version_id"]
                    and s["analysis_job_uuid"] == (analysis.job_uuid if analysis else None)
                    and s["analysis_basis_token"] == basis and s["status"] in {"completed", "in_progress"}] if analysis and analysis.completed else []
        session = matching[0] if matching else None
        completed = bool(session and session["status"] == "completed")
        findings, pending_count, confirmed_count, pending_checks = [], None, None, None
        if analysis and analysis.completed:
            coverage = build_coverage(analysis.result_meta.get("obligation_coverage"))
            current_decisions = {d["obligation_id"]: d for d in obligations
                                 if session and d["session_id"] == session["session_id"]}
            if coverage.available:
                pending_checks = len(unhandled_coverage_obligations(list(coverage.items), current_decisions))
            payload = json_value(run.get("raw_response"), {})
            payload = payload if isinstance(payload, dict) else {}
            projected = issue_workflow_store.project_job_issue_decisions(analysis.job_uuid, payload, _state=workflow)
            decisions = {d["issue_id"]: d["status"] for d in projected}
            problems = collect_review_problems(job_uuid=analysis.job_uuid, ai_findings=run.get("ai_findings"),
                rule_findings=run.get("rule_findings"), merged_result=run.get("merged_result"), structured_ingest=analysis.structured_ingest)
            findings = [normalize_finding(asdict(p)) for p in problems if p.is_formal and decisions.get(p.issue_id) in CONFIRMED]
            pending_count = sum(p.severity != "info" and decisions.get(p.issue_id) not in CONFIRMED | {"no_issue"} for p in problems)
            for decision in obligations:
                if session and decision["session_id"] == session["session_id"] and decision["decision"] == "verified_issue":
                    findings.append({"issue_id": "obligation:" + decision["obligation_id"], "title": decision.get("note") or "人工补核确认问题",
                                     "severity": "manual_review", "source": "obligation", "evidence_reference": decision.get("evidence_reference")})
            confirmed_count = len(findings)
            snapshot = json_value(session.get("review_result"), {}) if completed else {}
            if "confirmed_findings" in snapshot:
                findings = [normalize_finding(f) for f in snapshot["confirmed_findings"]]
                confirmed_count = len(findings)
            elif completed:
                # 旧快照只有计数时，保留计数和“不完整”标识，不凭标题编造问题。
                counts = snapshot.get("issue_counts") or {}
                manual = (snapshot.get("coverage") or {}).get("manual_decisions") or {}
                confirmed_count = max(confirmed_count, int(counts.get("confirmed", 0)) + int(counts.get("in_package", 0)) + int(manual.get("verified_issue", 0)))
            if completed:
                pending_count = 0
        slot_rounds = [r for r in rounds if r["slot_key"] == slot]
        item = {"slot_id": slot, "organization_id": row["subject_org_id"], "organization_name": row["subject_org_name"],
            "subject_kind": row["subject_kind"], "department_id": row["department_org_id"], "department_name": row["department_name"],
            "fiscal_year": row["fiscal_year"], "report_kind": row["report_kind"], "status": "failed" if analysis and normalize_job_status(analysis.status) == "failed" else row["slot_status"],
            "applicability_status": row["applicability_status"], "uploaded": row["current_document_version_id"] is not None,
            "document_version_id": row["current_document_version_id"], "job_id": analysis.job_uuid if analysis else None,
            "basis_token": basis, "filename": row.get("original_filename"), "due_at": iso(row.get("due_at")),
            "review_session_id": (session or {}).get("session_id"),
            "overdue": overdue(row.get("due_at"), row["current_document_version_id"] is not None, row["applicability_status"]),
            "analyzed": bool(analysis and analysis.completed), "review_status": "completed" if completed else "in_progress" if session else "pending" if analysis and analysis.completed else "waiting_analysis",
            "confirmed_count": confirmed_count, "pending_count": pending_count, "pending_checks": pending_checks, "findings": findings,
            "rounds_count": len(slot_rounds), "version_count": version_counts.get(slot, 0),
            "history_incomplete": version_counts.get(slot, 0) > 1 and not slot_rounds,
            "reviewer": (session or {}).get("completed_by") or (session or {}).get("started_by"),
            "reviewed_at": iso((session or {}).get("completed_at")), "updated_at": iso(row.get("slot_updated_at")),
            "_sessions": history, "_rounds": slot_rounds,
            "_pending_findings": ([normalize_finding(asdict(p)) for p in problems if p.severity != "info" and decisions.get(p.issue_id) not in CONFIRMED | {"no_issue"}] if analysis and analysis.completed and not completed else [])}
        items.append(item)
    return items


def public_item(item: dict) -> dict:
    return {key: value for key, value in item.items() if not key.startswith("_") and key != "findings"}


async def query(conn: Any, *, visible_org_ids: list[str] | None, scope_ids: list[str] | None,
                scope_id: str, fiscal_year: int, report_kind: str, view: str,
                keyword: str = "", department_id: str = "", status: str = "all", severity: str = "all",
                category_filter: str = "", sort: str = "confirmed", page: int = 1, page_size: int = 20,
                export: bool = False) -> dict:
    items = await build_items(conn, await load_rows(conn, visible_org_ids=visible_org_ids, scope_ids=scope_ids,
                                                 fiscal_year=fiscal_year, report_kind=report_kind))
    baseline = await conn.fetchrow("SELECT * FROM supervision_baselines WHERE scope_org_id=$1 AND fiscal_year=$2 AND report_kind=$3 AND COALESCE(to_jsonb(supervision_baselines)->>'task_id','')='' ORDER BY created_at DESC,id DESC LIMIT 1", scope_id, fiscal_year, report_kind) if scope_id else None
    baseline_ids = set(json_value(baseline["slot_ids"], [])) if baseline else None
    # 清单引用的材料若已缺失，不能把分母偷偷缩小。
    if baseline_ids is not None and baseline_ids != {i["slot_id"] for i in items}:
        baseline_ids = None
    summary, departments = summarize(items, baseline_ids)
    selected = [i for i in items if not department_id or i["department_id"] == department_id or i["organization_id"] == department_id]
    def matches(text: str) -> bool:
        return not keyword or keyword.casefold() in text.casefold()
    selected = [i for i in selected if matches(f"{i['organization_name']} {i['department_name'] or ''} {i['filename'] or ''}")]
    if status == "missing":
        selected = [i for i in selected if not i["uploaded"] and i["applicability_status"] != "not_applicable" and (baseline_ids is None or i["slot_id"] in baseline_ids)]
    elif status == "overdue":
        selected = [i for i in selected if i["overdue"]]
    elif status == "repeat":
        selected = [i for i in selected if i["rounds_count"] >= 2 and i.get("confirmed_count")]
    elif status == "issues":
        selected = [i for i in selected if i.get("confirmed_count")]
    elif status == "waiting":
        selected = [i for i in selected if i["rounds_count"] and i["review_status"] != "completed"]
    selected.sort(key=lambda i: (i.get("confirmed_count") or 0, i["rounds_count"]), reverse=True)
    ranking = sorted(departments, key=lambda d: d.get("problem_rate") if d.get("problem_rate") is not None else -1, reverse=True) if sort == "rate" else sorted(departments, key=lambda d: d.get("severe" if sort == "severe" else "confirmed", 0), reverse=True)
    if view == "departments":
        display = [d for d in ranking if matches(d["name"]) and (not department_id or d["id"] == department_id)]
        if status == "missing":
            display = [d for d in display if d["missing"]] if baseline_ids is not None else [d for d in display if any(not i["uploaded"] for i in items if i["department_id"] == d["id"])]
        elif status == "issues":
            display = [d for d in display if d["confirmed"]]
        elif status == "repeat":
            display = [d for d in display if d["repeat_materials"]]
        elif status in {"overdue", "waiting"}:
            matching_ids = {i["department_id"] or i["organization_id"] for i in selected}
            display = [d for d in display if d["id"] in matching_ids]
    elif view == "issues":
        display = []
        for item in items:
            if department_id and item["department_id"] != department_id and item["organization_id"] != department_id:
                continue
            for finding in item["findings"]:
                if (severity == "all" or finding.get("severity") == severity) and (not category_filter or category(finding["title"]) == category_filter) and matches(item["organization_name"] + finding["title"]):
                    display.append({**finding, "slot_id": item["slot_id"], "organization_name": item["organization_name"],
                        "department_name": item["department_name"], "job_id": item["job_id"], "category": category(finding["title"])})
    else:
        display = [public_item(i) for i in selected if view != "remediation" or i["rounds_count"] or i["history_incomplete"]]
    return {"summary": summary, "ranking": ranking[:10], "departments": [{"id": d["id"], "name": d["name"]} for d in departments],
            "priority": [public_item(i) for i in items if i["rounds_count"] >= 2 and i.get("confirmed_count")][:5],
            "list": paginate(display, page, page_size), "export_rows": display if export else [], "meta": {"baseline_ready": baseline_ids is not None,
            "baseline_by": baseline["confirmed_by"] if baseline else None, "baseline_at": iso(baseline["created_at"]) if baseline else None,
            "data_basis": "confirmed_expected_list" if baseline_ids is not None else "existing_slots_only",
            "scope_id": scope_id, "fiscal_year": fiscal_year, "report_kind": report_kind}}


async def material_detail(conn: Any, slot_id: str, visible_org_ids: list[str] | None) -> dict | None:
    items = await build_items(conn, await load_rows(conn, visible_org_ids=visible_org_ids, slot_id=slot_id))
    if not items:
        return None
    item = items[0]
    history = []
    for session in reversed(item["_sessions"]):
        if not session.get("completed_at"):
            continue
        result = json_value(session.get("review_result"), {})
        counts = result.get("issue_counts") or {}
        manual = (result.get("coverage") or {}).get("manual_decisions") or {}
        history.append({"session_id": session["session_id"], "document_version_id": session["document_version_id"],
            "basis_token": session["analysis_basis_token"], "reviewed_at": iso(session["completed_at"]), "reviewer": session["completed_by"],
            "confirmed_count": len(result["confirmed_findings"]) if "confirmed_findings" in result else int(counts.get("confirmed", 0)) + int(counts.get("in_package", 0)) + int(manual.get("verified_issue", 0)),
            "findings": ([normalize_finding(f) for f in result["confirmed_findings"]]
                         if "confirmed_findings" in result else None),
            "snapshot_complete": "confirmed_findings" in result})
    rounds = []
    for row in item["_rounds"]:
        verification = json_value(row.get("verification_result"), None)
        rounds.append({"id": row["round_id"], "round_number": row["round_number"], "document_version_id": row["document_version_id"],
            "feedback_session_id": str(row["feedback_session_id"]), "submitted_by": row["submitted_by"], "submitted_at": iso(row["submitted_at"]),
            "note": row["note"], "verification": verification, "verified_at": iso(row.get("verified_at")), "verified_by": row.get("verified_by")})
    versions = [dict(v) for v in await conn.fetch("SELECT id,original_filename,created_at FROM fiscal_document_versions WHERE slot_id::text=$1 ORDER BY created_at,id", slot_id)]
    for review in history:
        review["version_number"] = next((index + 1 for index, version in enumerate(versions) if version["id"] == review["document_version_id"]), None)
    return {"material": public_item(item), "findings": item["findings"], "reviews": history, "rounds": rounds,
            "versions": [{"id": v["id"], "filename": v["original_filename"], "created_at": iso(v["created_at"])} for v in versions]}
