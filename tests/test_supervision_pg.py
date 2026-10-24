"""主管查询在随机独立 schema 验证 SQL、可见范围和大数量分页。"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from src.services import issue_workflow_store
from src.services.supervision_query import available_periods, query, material_detail
from tests.test_material_ledger_pg import ledger_db, _conn, _insert_slot
from tests.test_material_ledger_detail_pg import _insert_version, _insert_job

pytestmark = pytest.mark.real_database


@pytest.mark.asyncio
async def test_available_periods_respect_scope_and_expected_materials(ledger_db):  # noqa: F811 -- 复用导入的 pytest fixture
    schema, pool = ledger_db
    async with _conn(schema, pool) as conn:
        await _insert_slot(conn, fiscal_year=2026, report_kind="budget", subject_org_id="unit-a")
        slot = await _insert_slot(conn, fiscal_year=2025, report_kind="final", subject_org_id="unit-a")
        version = await _insert_version(conn, slot_id=slot, file_hash="period-test", created_at=datetime.now(timezone.utc))
        await conn.execute("UPDATE material_slots SET current_document_version_id=$2 WHERE id::text=$1", slot, version)
        await _insert_slot(conn, fiscal_year=2027, report_kind="final", subject_org_id="unit-b", department_org_id="outside", jurisdiction_org_id="outside")
        assert await available_periods(conn, visible_org_ids=["unit-a"], scope_ids=None) == [
            {"fiscal_year": 2026, "report_kind": "budget", "material_count": 1, "uploaded": 0},
            {"fiscal_year": 2025, "report_kind": "final", "material_count": 1, "uploaded": 1},
        ]
        assert await available_periods(conn, visible_org_ids=["unit-a"], scope_ids=["unit-b"]) == []
        assert await available_periods(conn, visible_org_ids=[], scope_ids=None) == []


@pytest.mark.asyncio
async def test_large_scoped_materials_and_baseline(ledger_db, monkeypatch, tmp_path):  # noqa: F811 -- 复用导入的 pytest fixture
    schema, pool = ledger_db
    monkeypatch.setattr(issue_workflow_store, "_store_path", lambda: tmp_path / "workflow.json")
    async with _conn(schema, pool) as conn:
        ids = []
        for i in range(351):
            ids.append(await _insert_slot(conn, fiscal_year=2026, report_kind="budget", subject_org_id=f"unit-{i}", department_org_id=f"dept-{i // 4}", status="not_due"))
        await _insert_slot(conn, fiscal_year=2026, subject_org_id="outside", department_org_id="outside-dept", jurisdiction_org_id="other")
        kwargs = dict(visible_org_ids=[f"unit-{i}" for i in range(351)], scope_ids=None, scope_id="district", fiscal_year=2026, report_kind="budget", view="materials")
        before = await query(conn, **kwargs)
        assert before["summary"]["expected"] is None and before["list"]["total"] == 351
        await conn.execute("INSERT INTO supervision_baselines(scope_org_id,fiscal_year,report_kind,slot_ids,confirmed_by,note) VALUES($1,2026,'budget',$2::jsonb,'reviewer','已核对完整名单')", "district", json.dumps(ids))
        after = await query(conn, **kwargs, page=18)
        assert after["meta"]["baseline_ready"]
        assert after["summary"]["expected"] == 351 and after["summary"]["missing"] == 351
        assert after["list"]["total_pages"] == 18 and len(after["list"]["items"]) == 11
        assert all(i["organization_id"] != "outside" for i in after["list"]["items"])
        await _insert_slot(conn, fiscal_year=2026, subject_org_id="unit-new", department_org_id="dept-new", report_kind="budget")
        invalidated = await query(conn, **{**kwargs, "visible_org_ids": kwargs["visible_org_ids"] + ["unit-new"]})
        assert not invalidated["meta"]["baseline_ready"]


@pytest.mark.asyncio
async def test_current_version_and_review_generation_are_exact(ledger_db, monkeypatch, tmp_path):  # noqa: F811 -- 复用导入的 pytest fixture
    schema, pool = ledger_db
    monkeypatch.setattr(issue_workflow_store, "_store_path", lambda: tmp_path / "workflow.json")
    async with _conn(schema, pool) as conn:
        slot = await _insert_slot(conn, fiscal_year=2026, report_kind="budget", status="review_required")
        now = datetime.now(timezone.utc)
        old = await _insert_version(conn, slot_id=slot, file_hash="old", created_at=now - timedelta(days=2))
        current = await _insert_version(conn, slot_id=slot, file_hash="current", created_at=now)
        await conn.execute("UPDATE material_slots SET current_document_version_id=$2 WHERE id::text=$1", slot, current)
        for version, job in [(old, "old-job"), (current, "current-job")]:
            job_id = await _insert_job(conn, job_uuid=job, metadata={"structured_ingest": {"document_version_id": version}})
            await conn.execute("UPDATE analysis_jobs SET analysis_revision=1 WHERE id=$1", job_id)
            await conn.execute("INSERT INTO analysis_results(job_id,rule_findings,raw_response) VALUES($1,'[]','{}')", job_id)
        await conn.execute("INSERT INTO review_sessions(slot_id,document_version_id,analysis_job_uuid,analysis_basis_token,status,started_by,completed_by,completed_at,review_result) VALUES($1::uuid,$2,'old-job','old-job:1','completed','reviewer','reviewer',NOW(),$3::jsonb)", slot, old,
                           json.dumps({"confirmed_findings": [{"issue_id": "old-issue", "title": "旧版问题", "severity": "high"}]}))
        result = await query(conn, visible_org_ids=None, scope_ids=None, scope_id="", fiscal_year=2026, report_kind="budget", view="materials")
        assert result["summary"]["confirmed"] == 0, "旧文件上的确认问题不得累计进当前总量"
        assert result["summary"]["reviewed"] == 0, "旧版本已复核不能继承给新版本"
        assert result["summary"]["repeat_materials"] == 0, "两个文件版本不等于整改两轮"
        detail = await material_detail(conn, slot, None)
        assert detail["material"]["version_count"] == 2 and detail["reviews"][0]["confirmed_count"] == 1
        assert detail["material"]["history_incomplete"]

        # 同一版本重跑过程中，即使旧 completed 会话尚未失效，也不能沿用结论。
        await conn.execute("INSERT INTO review_sessions(slot_id,document_version_id,analysis_job_uuid,analysis_basis_token,status,started_by,completed_by,completed_at,review_result) VALUES($1::uuid,$2,'current-job','current-job:1','completed','reviewer','reviewer',NOW(),$3::jsonb)", slot, current,
                           json.dumps({"confirmed_findings": [{"issue_id": "stale", "title": "旧代际", "severity": "high"}]}))
        await conn.execute("UPDATE analysis_jobs SET status='processing' WHERE job_uuid='current-job'")
        processing = await material_detail(conn, slot, None)
        assert processing["material"]["review_status"] == "waiting_analysis"
        assert processing["material"]["confirmed_count"] is None


@pytest.mark.asyncio
async def test_unfinished_coverage_is_separate_from_issue_leads(ledger_db, monkeypatch, tmp_path):  # noqa: F811 -- 复用导入的 pytest fixture
    from tests.test_obligation_review_pg import _coverage_payload

    schema, pool = ledger_db
    monkeypatch.setattr(issue_workflow_store, "_store_path", lambda: tmp_path / "workflow.json")
    async with _conn(schema, pool) as conn:
        slot = await _insert_slot(conn, fiscal_year=2026, report_kind="budget", status="review_required")
        version = await _insert_version(conn, slot_id=slot, file_hash="coverage", created_at=datetime.now(timezone.utc))
        await conn.execute("UPDATE material_slots SET current_document_version_id=$2 WHERE id::text=$1", slot, version)
        job_id = await _insert_job(conn, job_uuid="coverage-job", metadata={"structured_ingest": {"document_version_id": version}, "result_meta": _coverage_payload(["a", "b"])})
        await conn.execute("UPDATE analysis_jobs SET analysis_revision=1 WHERE id=$1", job_id)
        await conn.execute("INSERT INTO analysis_results(job_id,rule_findings,raw_response) VALUES($1,'[]','{}')", job_id)
        before = await material_detail(conn, slot, None)
        assert before["material"]["pending_count"] == 0
        assert before["material"]["pending_checks"] == 2
        session = await conn.fetchval("INSERT INTO review_sessions(slot_id,document_version_id,analysis_job_uuid,analysis_basis_token,status,started_by) VALUES($1::uuid,$2,'coverage-job','coverage-job:1','in_progress','reviewer') RETURNING id", slot, version)
        await conn.execute("INSERT INTO review_obligation_decisions(review_session_id,slot_id,obligation_id,decision,note,evidence_reference,reviewer) VALUES($1,$2::uuid,'a','verified_ok','已核对','原文','reviewer')", session, slot)
        after = await material_detail(conn, slot, None)
        assert after["material"]["pending_checks"] == 1


@pytest.mark.asyncio
async def test_formal_round_register_verify_and_stale_session(ledger_db, monkeypatch, tmp_path):  # noqa: F811 -- 复用导入的 pytest fixture
    from httpx import AsyncClient, ASGITransport
    from api.main import app
    from api.routes import supervision
    from api.material_access import MaterialAccessScope

    schema, pool = ledger_db
    monkeypatch.setattr(issue_workflow_store, "_store_path", lambda: tmp_path / "workflow.json")
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(supervision, "context", lambda *args: ({"username": "reviewer"}, MaterialAccessScope(), None, []))
    async with _conn(schema, pool) as conn:
        async def using_connection(callback):
            return await callback(conn)
        monkeypatch.setattr(supervision, "with_connection", using_connection)
        now = datetime.now(timezone.utc)
        slot = await _insert_slot(conn, fiscal_year=2026, report_kind="budget", status="completed")
        old = await _insert_version(conn, slot_id=slot, file_hash="old-contents", created_at=now - timedelta(days=4))
        current = await _insert_version(conn, slot_id=slot, file_hash="new-contents", created_at=now - timedelta(days=1))
        await conn.execute("UPDATE material_slots SET current_document_version_id=$2 WHERE id::text=$1", slot, current)
        snapshots = [[{"issue_id": "old1", "title": "旧问题1", "severity": "high"}, {"issue_id": "old2", "title": "旧问题2", "severity": "high"}],
                     [{"issue_id": "new1", "title": "旧问题仍存在", "severity": "high"}, {"issue_id": "new2", "title": "新增问题", "severity": "low"}]]
        session_ids = []
        for index, (version, job) in enumerate([(old, "feedback-job"), (current, "verified-job")]):
            job_id = await _insert_job(conn, job_uuid=job, metadata={"structured_ingest": {"document_version_id": version}})
            await conn.execute("UPDATE analysis_jobs SET analysis_revision=1 WHERE id=$1", job_id)
            await conn.execute("INSERT INTO analysis_results(job_id,rule_findings,raw_response) VALUES($1,'[]','{}')", job_id)
            session_ids.append(str(await conn.fetchval("INSERT INTO review_sessions(slot_id,document_version_id,analysis_job_uuid,analysis_basis_token,status,started_by,completed_by,completed_at,review_result) VALUES($1::uuid,$2,$3,$4,'completed','reviewer','reviewer',$5,$6::jsonb) RETURNING id",
                slot, version, job, job + ":1", now - timedelta(days=2) if index == 0 else now, json.dumps({"confirmed_findings": snapshots[index]}))))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            registered = await client.post(f"/api/supervision/materials/{slot}/rounds", json={"document_version_id": current, "note": "收到反馈后正式提交新版整改材料"})
            assert registered.status_code == 200, registered.text
            round_id = registered.json()["data"]["id"]
            repeat = await client.post(f"/api/supervision/materials/{slot}/rounds", json={"document_version_id": current, "note": "重复提交登记，不增加轮次"})
            assert repeat.json()["data"]["replayed"]
            body = {"review_session_id": session_ids[1], "comparisons": [{"previous_issue_id": "old1", "resolution": "remains", "current_issue_id": "new1"}, {"previous_issue_id": "old2", "resolution": "fixed"}], "note": "已逐项核对新版原文表格及对应说明，确认旧问题二已修复"}
            verified = await client.post(f"/api/supervision/materials/{slot}/rounds/{round_id}/verify", json=body)
            assert verified.status_code == 200, verified.text
            assert (verified.json()["data"]["fixed"], verified.json()["data"]["remaining"], verified.json()["data"]["new"]) == (1, 1, 1)
            detail = await client.get(f"/api/supervision/materials/{slot}")
            assert detail.json()["data"]["material"]["rounds_count"] == 1
            overview = await client.get("/api/supervision", params={"fiscal_year": 2026, "report_kind": "budget", "view": "issues", "page_size": 1})
            assert overview.status_code == 200, overview.text
            assert overview.json()["data"]["list"]["total"] == 2
            assert overview.json()["data"]["meta"]["available_periods"] == [{"fiscal_year": 2026, "report_kind": "budget", "material_count": 1, "uploaded": 1}]
            exported = await client.get("/api/supervision", params={"fiscal_year": 2026, "report_kind": "budget", "view": "issues", "page_size": 1, "format": "csv"})
            import csv
            import io
            assert len(list(csv.reader(io.StringIO(exported.text.lstrip("\ufeff"))))) - 3 == 2, "导出必须包含全部匹配问题，不能只导出第一页"
            # 同一代际显式重新复核，也必须选择新会话，不能复用旧签署结论。
            await conn.execute("UPDATE review_sessions SET status='invalidated',invalidated_at=NOW(),invalidated_reason='reopened' WHERE id::text=$1", session_ids[1])
            await conn.execute("INSERT INTO review_sessions(slot_id,document_version_id,analysis_job_uuid,analysis_basis_token,status,started_by,completed_by,completed_at,review_result) VALUES($1::uuid,$2,'verified-job','verified-job:1','completed','reviewer','reviewer',NOW(),$3::jsonb)", slot, current, json.dumps({"confirmed_findings": snapshots[1]}))
            stale = await client.post(f"/api/supervision/materials/{slot}/rounds/{round_id}/verify", json=body)
            assert stale.status_code == 409
