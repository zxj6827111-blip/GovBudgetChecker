"""DH-02/03 诊断字段在隔离 PostgreSQL 上的保存→读回→复核→导出贯通。

纯函数测试只能证明字段在内存对象之间传递；本文件回答的是另一件事：
诊断字段写进 ``analysis_jobs.metadata.result_meta`` 后，新的连接读回时是否
仍是同一份内容，真实复核服务 ``load_review_by_slot`` 是否原样透出，导出
端点（``/api/reports/download`` 的 JSON 分支）是否仍能拿到这些字段。

隔离方式与 ``tests/test_obligation_review_pg.py`` 相同：随机 schema +
真实迁移，跑完 ``DROP SCHEMA CASCADE``。只使用显式配置的
``GOVBUDGET_TEST_DATABASE_URL``，绝不接触开发/生产库。
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import pytest

from src.db.migrations import run_migrations
from src.services import analysis_result_store, review_lifecycle_service

pytestmark = pytest.mark.real_database

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
DISTRICT = "district-putuo"
DEPT = "dept-planning"
UNIT = "unit-planning-enforcement"


async def _insert_slot(conn) -> str:
    payload: Dict[str, Any] = {
        "slot_key": f"key-{uuid.uuid4().hex}",
        "mapping_key": "",
        "jurisdiction_org_id": DISTRICT,
        "jurisdiction_name": "上海市普陀区",
        "department_org_id": DEPT,
        "department_name": "上海市普陀区规划和自然资源局",
        "subject_org_id": UNIT,
        "subject_org_name": "上海市普陀区规划和自然资源局执法大队",
        "subject_kind": "unit",
        "subject_level": "unit",
        "material_scope": "unit_self",
        "caliber": "self",
        "fiscal_year": 2025,
        "report_kind": "final",
        "due_at": None,
        "status": "uploaded",
        "status_reason": "awaiting_analysis",
        "updated_at": NOW,
    }
    columns = ", ".join(payload)
    placeholders = ", ".join(f"${index}" for index in range(1, len(payload) + 1))
    row = await conn.fetchrow(
        f"INSERT INTO material_slots ({columns}) VALUES ({placeholders})"
        " RETURNING id::text AS slot_id",
        *payload.values(),
    )
    return str(row["slot_id"])


async def _insert_version(conn, *, slot_id: str, file_hash: str) -> int:
    org_unit_id = await conn.fetchval(
        "INSERT INTO org_units (org_name) VALUES ($1) RETURNING id",
        f"unit-{uuid.uuid4().hex[:8]}",
    )
    document_id = await conn.fetchval(
        "INSERT INTO fiscal_documents (org_unit_id, fiscal_year, doc_type)"
        " VALUES ($1, $2, $3) RETURNING id",
        org_unit_id,
        2025,
        f"final-{uuid.uuid4().hex[:8]}",
    )
    return int(
        await conn.fetchval(
            """
            INSERT INTO fiscal_document_versions
                (document_id, file_hash, storage_key, created_at, slot_id)
            VALUES ($1, $2, $3, $4, $5::uuid)
            RETURNING id
            """,
            document_id,
            file_hash,
            f"{uuid.uuid4().hex}/material.pdf",
            NOW,
            slot_id,
        )
    )


def _text_operand(*, role: str = "住房公积金", page: int = 20) -> Dict[str, Any]:
    return {
        "source": "text",
        "role": role,
        "section": "一般公共预算财政拨款支出决算情况说明",
        "page": page,
        "row_key": {"klass": "住房保障支出", "kuan": "住房改革支出", "xiang": "住房公积金"},
        "column": None,
        "fund_scope": "一般公共预算",
        "stage": "决算",
        "year": 2025,
    }


def _coverage() -> Dict[str, Any]:
    new_instance = {
        "obligation_id": "OBL-TXT-T5",
        "group_id": "TABLE_TEXT",
        "group_title": "表文关系",
        "title": "一般公共预算支出决算表 与 功能分类说明一致（含科目名称）",
        "status": "insufficient_data",
        "reason": "insufficient_data",
        "reason_label": "取数不足",
        "detail": "正在核对：一般公共预算支出决算表 与 功能分类说明一致。",
        "basis": "AGENTS.md 决算必查勾稽 D-005",
        "checkers": ["V33-106", "V33-222", "V33-227"],
        "blocks_gate": True,
        "requires_ai": False,
        "input_gaps": [],
        "evidence_kind": "locatable",
        "gap_note": "",
        "reason_codes": ["subject_name_unlocated"],
        "blocked_by": [],
        "missing_operands": [_text_operand()],
        "candidates": [{
            "source": "text",
            "section": "一般公共预算财政拨款支出决算情况说明",
            "page": 20,
            "span": [104, 133],
            "value": "-住房公积金",
        }],
        "next_action": "human_confirm",
        "root_cause_key": "rc:dh02-persistence-test",
        # 合法的新结果：空列表必须保持 []，不能读成 None/旧结果。
        "legacy_coverage_terms": [],
    }
    old_instance = {
        "obligation_id": "OBL-OLD-LEGACY",
        "group_id": "TABLE_TEXT",
        "group_title": "表文关系",
        "title": "旧任务没有结构化诊断",
        "status": "insufficient_data",
        "reason": "insufficient_data",
        "reason_label": "取数不足",
        "detail": "说明中的款“旧科目名称”在表内找不到同名科目",
        "basis": "旧版依据",
        "checkers": [],
        "blocks_gate": True,
        "requires_ai": False,
        "input_gaps": [],
        "evidence_kind": "document_level",
        "gap_note": "",
        # 刻意不写 reason_codes/blocked_by/missing_operands/candidates/
        # next_action/root_cause_key/legacy_coverage_terms，模拟旧任务。
    }
    return {
        "catalog_version": "obligations-v12-checklist-global-20261022",
        "catalog_fingerprint": "fingerprint",
        "applicable_total": 2,
        "completed_total": 0,
        "not_applicable_total": 0,
        "unresolved_total": 2,
        "blocking_total": 2,
        "by_reason": {"insufficient_data": 2},
        "by_group": [],
        "instances": [new_instance, old_instance],
    }


def _receipts() -> list[Dict[str, Any]]:
    return [
        {
            "obligation_id": "OBL-TXT-T5",
            "rule_id": "V33-222",
            "sub_check_id": "OBL-TXT-T5:V33-222",
            "status": "insufficient_data",
            "reason_codes": ["subject_name_unlocated"],
            "blocked_by": [],
            "missing_operands": [_text_operand()],
            "candidates": [{
                "source": "text",
                "section": "一般公共预算财政拨款支出决算情况说明",
                "page": 20,
                "span": [104, 133],
                "value": "-住房公积金",
            }],
            "next_action": "human_confirm",
            "root_cause_key": "rc:dh02-persistence-receipt",
            "operand_fact_ids": [],
            "incomplete_reason": "V33-222 · OBL-TXT-T5:V33-222 未完成。",
        },
        {
            "obligation_id": "OBL-TXT-T5",
            "rule_id": "V33-222",
            "sub_check_id": "relation:completed",
            "status": "pass",
            "reason_codes": [],
            "blocked_by": [],
            "missing_operands": [],
            "candidates": [],
            "next_action": None,
            "root_cause_key": None,
            "operand_fact_ids": ["f1", "f1", "f2"],
            "incomplete_reason": None,
        },
    ]


@pytest.fixture
async def diagnostic_db(real_database_url, monkeypatch):
    """随机 schema + 真实迁移，跑完删除；不接触 public。"""
    from src.db.connection import DatabaseConnection

    schema = f"dh02_diag_test_{uuid.uuid4().hex[:12]}"
    monkeypatch.setenv("PG_SCHEMA", schema)
    DatabaseConnection._pool = None
    pool = await DatabaseConnection.initialize(real_database_url)
    assert DatabaseConnection.get_schema() == schema
    async with pool.acquire() as conn:
        await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    await run_migrations()
    try:
        yield schema, pool
    finally:
        DatabaseConnection._pool = None
        try:
            async with pool.acquire() as conn:
                await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        finally:
            await pool.close()


@asynccontextmanager
async def _conn(schema: str, pool):
    async with pool.acquire() as conn:
        await conn.execute(f'SET search_path TO "{schema}", public')
        yield conn


async def _persist_job(conn, *, job_uuid: str, version_id: int) -> None:
    payload = {
        "job_id": job_uuid,
        "filename": "material.pdf",
        "status": "done",
        "mode": "dual",
        "structured_ingest": {"document_version_id": version_id},
        "result": {
            "ai_findings": [],
            "rule_findings": [],
            "merged": {},
            "meta": {
                "started_at": NOW.isoformat(),
                "completed_at": (NOW + timedelta(minutes=2)).isoformat(),
                "obligation_coverage": _coverage(),
                "check_receipts": _receipts(),
                "document_profile": {"kind": "final"},
            },
        },
    }
    assert await analysis_result_store.persist_analysis_job_snapshot(
        payload, include_results=True
    )


async def test_save_read_review_and_export_keep_diagnostic_fields(
    diagnostic_db, monkeypatch
) -> None:
    schema, pool = diagnostic_db
    async with _conn(schema, pool) as conn:
        slot_id = await _insert_slot(conn)
        version_id = await _insert_version(
            conn, slot_id=slot_id, file_hash="b" * 64
        )
        job_uuid = f"job-{uuid.uuid4().hex[:12]}"
        await _persist_job(conn, job_uuid=job_uuid, version_id=version_id)
        await conn.execute(
            "UPDATE material_slots SET current_document_version_id = $1,"
            " status = 'review_required' WHERE id = $2::uuid",
            version_id,
            slot_id,
        )

    # 1) 保存→读回：新连接、真实存储层。
    detail = await analysis_result_store.get_persisted_analysis_job_detail(job_uuid)
    assert detail is not None and detail.get("available") is True
    stored = detail["result_meta"]
    stored_items = {
        item["obligation_id"]: item
        for item in stored["obligation_coverage"]["instances"]
    }
    assert stored_items["OBL-TXT-T5"]["reason_codes"] == ["subject_name_unlocated"]
    assert stored_items["OBL-TXT-T5"]["missing_operands"] == [_text_operand()]
    assert stored_items["OBL-TXT-T5"]["candidates"][0]["value"] == "-住房公积金"
    assert stored_items["OBL-TXT-T5"]["next_action"] == "human_confirm"
    assert stored_items["OBL-TXT-T5"]["root_cause_key"] == "rc:dh02-persistence-test"
    assert stored_items["OBL-TXT-T5"]["legacy_coverage_terms"] == []
    assert "reason_codes" not in stored_items["OBL-OLD-LEGACY"]
    assert "legacy_coverage_terms" not in stored_items["OBL-OLD-LEGACY"]

    # 2) 真实复核接口：GET /api/reviews/{slot} 的服务层。
    async with _conn(schema, pool) as conn:
        review = await review_lifecycle_service.load_review_by_slot(conn, slot_id)
        assert review.obligation_review is not None
        by_id = {item.obligation_id: item for item in review.obligation_review.items}
        new_item = by_id["OBL-TXT-T5"]
        assert new_item.reason_codes == ["subject_name_unlocated"]
        assert new_item.missing_operands == [_text_operand()]
        assert new_item.candidates[0]["value"] == "-住房公积金"
        assert new_item.next_action == "human_confirm"
        assert new_item.root_cause_key == "rc:dh02-persistence-test"
        assert new_item.legacy_coverage_terms == []
        old_item = by_id["OBL-OLD-LEGACY"]
        assert old_item.reason_codes == []
        assert old_item.missing_operands == []
        assert old_item.candidates == []
        assert old_item.next_action is None
        assert old_item.legacy_coverage_terms is None

        started = await review_lifecycle_service.start_review(
            conn, slot_id, actor="persistence-reviewer"
        )
        assert started.session.status == "in_progress"
        decided = await review_lifecycle_service.decide_obligation(
            conn, slot_id, obligation_id="OBL-TXT-T5",
            decision="pending", actor="persistence-reviewer",
        )
        assert decided.decision.decision == "pending"
        reloaded = await review_lifecycle_service.load_review_by_slot(conn, slot_id)
        reloaded_item = {
            item.obligation_id: item for item in reloaded.obligation_review.items
        }["OBL-TXT-T5"]
        assert reloaded_item.reason_codes == ["subject_name_unlocated"]
        assert reloaded_item.missing_operands == [_text_operand()]

    # 3) 导出端点 JSON 分支：走真实 download_report 函数与 DB 读回结果。
    async with _conn(schema, pool) as conn:
        revision = await conn.fetchval(
            "SELECT analysis_revision FROM analysis_jobs WHERE job_uuid = $1",
            job_uuid,
        )
    published = {
        "job_id": job_uuid,
        "analysis_revision": revision,
        "result": {
            "ai_findings": [],
            "rule_findings": [],
            "merged": {},
            "meta": stored,
        },
        "structured_ingest": {"document_version_id": version_id},
        "status": "done",
    }

    from api.routes import reports as reports_routes

    def fake_access(request, requested_job_id):
        assert requested_job_id == job_uuid
        return None, None, None, published

    async def fake_read(job_id, revision):
        assert job_id == job_uuid
        return published

    monkeypatch.setattr(reports_routes, "require_job_access", fake_access)
    monkeypatch.setattr(
        "src.services.analysis_fact_snapshot.read_database_result", fake_read
    )
    monkeypatch.setattr(
        reports_routes, "_hydrate_version_bound_decisions",
        lambda job_id, payload: payload,
    )
    exported = await reports_routes.download_report(
        request=None, job_id=job_uuid, format="json", analysis_revision=None,
    )
    unfinished = {
        item["obligation_id"]: item for item in exported["unfinished_checks"]
    }
    new_export = unfinished["OBL-TXT-T5"]
    assert new_export["reason_codes"] == ["subject_name_unlocated"]
    assert new_export["missing_operands"] == [_text_operand()]
    assert new_export["candidates"][0]["value"] == "-住房公积金"
    assert new_export["next_action"] == "human_confirm"
    assert new_export["root_cause_key"] == "rc:dh02-persistence-test"
    assert new_export["legacy_coverage_terms"] == []
    old_export = unfinished["OBL-OLD-LEGACY"]
    assert old_export["reason_codes"] == []
    assert old_export["legacy_coverage_terms"] is None
