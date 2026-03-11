"""
Tests for runtime structured ingest metadata helpers.
"""

from __future__ import annotations

import io
import json
import os

import pytest
from fastapi import UploadFile

os.environ.setdefault("TESTING", "true")

from api import runtime


@pytest.mark.asyncio
async def test_store_upload_file_persists_status_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)

    upload = UploadFile(
        filename="sample_final_2025.pdf",
        file=io.BytesIO(
            b"%PDF-1.4\n"
            b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
            b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
            b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >> endobj\n"
            b"xref\n0 4\n0000000000 65535 f \n"
            b"trailer << /Size 4 /Root 1 0 R >>\nstartxref\n0\n%%EOF\n"
        ),
    )

    payload = await runtime.store_upload_file(
        upload,
        metadata={
            "organization_id": "org-1",
            "organization_name": "测试单位",
            "fiscal_year": "2025",
            "doc_type": "dept_final",
        },
    )

    status = runtime.get_job_status_payload(payload["job_id"])
    assert status["status"] == "uploaded"
    assert status["organization_id"] == "org-1"
    assert status["organization_name"] == "测试单位"
    assert status["fiscal_year"] == "2025"
    assert status["doc_type"] == "dept_final"
    assert float(status["version_created_at"]) > 0
    assert float(status["job_created_at"]) > 0


def test_get_job_review_payload_prefers_sidecar(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    job_dir = tmp_path / "job-1"
    job_dir.mkdir(parents=True)
    (job_dir / "status.json").write_text(
        json.dumps({"job_id": "job-1", "status": "done"}, ensure_ascii=False),
        encoding="utf-8",
    )
    runtime.write_structured_ingest_payload(
        job_dir,
        {
            "job_id": "job-1",
            "status": "done",
            "review_item_count": 1,
            "review_items": [{"id": "r1", "severity": "warn"}],
        },
    )

    payload = runtime.get_job_review_payload("job-1")

    assert payload["review_item_count"] == 1
    assert payload["review_items"][0]["id"] == "r1"


def test_collect_job_summary_includes_structured_ingest(tmp_path):
    job_dir = tmp_path / "job-2"
    job_dir.mkdir(parents=True)
    (job_dir / "status.json").write_text(
        json.dumps(
            {
                "job_id": "job-2",
                "status": "done",
                "progress": 100,
                "report_year": 2025,
                "report_kind": "final",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    runtime.write_structured_ingest_payload(
        job_dir,
        {
            "job_id": "job-2",
            "status": "done",
            "document_version_id": 12,
            "tables_count": 8,
            "recognized_tables": 7,
            "facts_count": 320,
            "document_profile": "canonical_nine_table",
            "missing_optional_tables": ["FIN_09_state_capital"],
            "review_item_count": 2,
            "low_confidence_item_count": 1,
            "ps_sync": {
                "report_id": "report-123",
                "table_data_count": 8,
                "line_item_count": 120,
                "match_mode": "organization_id",
            },
        },
    )

    summary = runtime.collect_job_summary(job_dir)

    assert summary["structured_ingest_status"] == "done"
    assert summary["structured_document_version_id"] == 12
    assert summary["structured_tables_count"] == 8
    assert summary["structured_document_profile"] == "canonical_nine_table"
    assert summary["structured_missing_optional_tables"] == ["FIN_09_state_capital"]
    assert summary["review_item_count"] == 2
    assert summary["structured_report_id"] == "report-123"
    assert summary["structured_table_data_count"] == 8
    assert summary["structured_line_item_count"] == 120
    assert summary["structured_sync_match_mode"] == "organization_id"


def test_legacy_job_link_backfills_organization_context(tmp_path, monkeypatch):
    class _DummyOrg:
        def __init__(self, org_id: str, name: str) -> None:
            self.id = org_id
            self.name = name

    class _DummyLink:
        def __init__(self, org_id: str, match_type: str, confidence: float) -> None:
            self.org_id = org_id
            self.match_type = match_type
            self.confidence = confidence

    class _DummyStorage:
        def get_job_org(self, job_id: str):
            if job_id == "job-legacy":
                return _DummyLink("org-legacy", "manual", 1.0)
            return None

        def get_by_id(self, org_id: str):
            if org_id == "org-legacy":
                return _DummyOrg("org-legacy", "上海市普陀区民政局")
            return None

    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    monkeypatch.setattr(runtime, "ORG_AVAILABLE", True)
    monkeypatch.setattr(runtime, "require_org_storage", lambda: _DummyStorage())
    runtime._JOB_SUMMARY_CACHE.clear()

    job_dir = tmp_path / "job-legacy"
    job_dir.mkdir(parents=True)
    (job_dir / "status.json").write_text(
        json.dumps(
            {
                "job_id": "job-legacy",
                "status": "done",
                "progress": 100,
                "report_year": 2026,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    status = runtime.get_job_status_payload("job-legacy")
    summary = runtime.collect_job_summary(job_dir)

    assert status["organization_id"] == "org-legacy"
    assert status["organization_name"] == "上海市普陀区民政局"
    assert status["organization_match_type"] == "manual"
    assert status["organization_match_confidence"] == 1.0
    assert summary["organization_name"] == "上海市普陀区民政局"
    assert summary["organization_match_type"] == "manual"


@pytest.mark.asyncio
async def test_start_analysis_preserves_organization_context(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)

    job_dir = tmp_path / "job-queued"
    job_dir.mkdir(parents=True)
    (job_dir / "sample_2025.pdf").write_bytes(
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >> endobj\n"
        b"xref\n0 4\n0000000000 65535 f \n"
        b"trailer << /Size 4 /Root 1 0 R >>\nstartxref\n0\n%%EOF\n"
    )
    runtime.write_json_file(
        job_dir / "status.json",
        {
            "job_id": "job-queued",
            "status": "uploaded",
            "organization_id": "org-1",
            "organization_name": "测试单位",
            "organization_match_type": "auto",
            "organization_match_confidence": 0.88,
            "checksum": "abc123",
            "fiscal_year": "2025",
            "doc_type": "dept_budget",
        },
    )

    async def _dummy_runner(_job_dir):
        return None

    class _DummyQueue:
        def __init__(self) -> None:
            self.enqueued: list[str] = []

        async def enqueue(self, job_id: str) -> None:
            self.enqueued.append(job_id)

    queue = _DummyQueue()
    monkeypatch.setattr(runtime, "_pipeline_runner", _dummy_runner)
    monkeypatch.setattr(runtime, "_job_queue", queue)

    payload = await runtime.start_analysis("job-queued", {"mode": "legacy"})

    assert payload["status"] == "started"
    assert queue.enqueued == ["job-queued"]

    status = runtime.read_json_file(job_dir / "status.json", default={})
    assert status["organization_id"] == "org-1"
    assert status["organization_name"] == "测试单位"
    assert status["organization_match_type"] == "auto"
    assert status["organization_match_confidence"] == 0.88
    assert status["checksum"] == "abc123"


@pytest.mark.asyncio
async def test_reanalyze_job_clones_pdf_and_reuses_analysis_context(tmp_path, monkeypatch):
    class _DummyOrg:
        def __init__(self, org_id: str, name: str) -> None:
            self.id = org_id
            self.name = name

    class _DummyStorage:
        def get_job_org(self, _job_id: str):
            return None

        def get_by_id(self, org_id: str):
            if org_id == "org-1":
                return _DummyOrg("org-1", "测试单位")
            return None

    class _DummyQueue:
        def __init__(self) -> None:
            self.enqueued: list[str] = []

        async def enqueue(self, job_id: str) -> None:
            self.enqueued.append(job_id)

    binding_calls: list[dict[str, object]] = []

    def _fake_set_job_organization(
        job_id: str,
        org_id: str,
        *,
        match_type: str = "manual",
        confidence: float = 1.0,
    ) -> dict[str, object]:
        binding_calls.append(
            {
                "job_id": job_id,
                "org_id": org_id,
                "match_type": match_type,
                "confidence": confidence,
            }
        )
        return {"organization_id": org_id}

    async def _dummy_runner(_job_dir):
        return None

    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    monkeypatch.setattr(runtime, "ORG_AVAILABLE", True)
    monkeypatch.setattr(runtime, "require_org_storage", lambda: _DummyStorage())
    monkeypatch.setattr(runtime, "set_job_organization", _fake_set_job_organization)
    monkeypatch.setattr(runtime, "_pipeline_runner", _dummy_runner)

    queue = _DummyQueue()
    monkeypatch.setattr(runtime, "_job_queue", queue)

    source_job_dir = tmp_path / "job-source"
    source_job_dir.mkdir(parents=True)
    pdf_bytes = (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >> endobj\n"
        b"xref\n0 4\n0000000000 65535 f \n"
        b"trailer << /Size 4 /Root 1 0 R >>\nstartxref\n0\n%%EOF\n"
    )
    (source_job_dir / "source_2025.pdf").write_bytes(pdf_bytes)
    runtime.write_json_file(
        source_job_dir / "status.json",
        {
            "job_id": "job-source",
            "status": "done",
            "progress": 100,
            "filename": "source_2025.pdf",
            "size": len(pdf_bytes),
            "saved_path": "job-source/source_2025.pdf",
            "checksum": "checksum-source",
            "organization_id": "org-1",
            "organization_name": "测试单位",
            "organization_match_type": "manual",
            "organization_match_confidence": 1.0,
            "fiscal_year": "2025",
            "doc_type": "dept_budget",
            "report_year": 2025,
            "use_local_rules": False,
            "use_ai_assist": True,
            "mode": "structured",
        },
    )

    payload = await runtime.reanalyze_job("job-source")

    assert payload["status"] == "started"
    assert payload["source_job_id"] == "job-source"
    assert payload["job_id"] != "job-source"
    assert queue.enqueued == [payload["job_id"]]
    assert binding_calls == [
        {
            "job_id": payload["job_id"],
            "org_id": "org-1",
            "match_type": "manual",
            "confidence": 1.0,
        }
    ]

    cloned_job_dir = tmp_path / payload["job_id"]
    cloned_pdf = cloned_job_dir / "source_2025.pdf"
    assert cloned_pdf.read_bytes() == pdf_bytes

    status = runtime.read_json_file(cloned_job_dir / "status.json", default={})
    assert status["status"] == "queued"
    assert status["filename"] == "source_2025.pdf"
    assert status["checksum"] == "checksum-source"
    assert status["organization_id"] == "org-1"
    assert status["fiscal_year"] == "2025"
    assert status["doc_type"] == "dept_budget"
    assert status["report_year"] == 2025
    assert status["use_local_rules"] is False
    assert status["use_ai_assist"] is True
    assert status["mode"] == "structured"


@pytest.mark.asyncio
async def test_reanalyze_all_jobs_skips_active_and_collects_results(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    monkeypatch.setattr(runtime, "ORG_AVAILABLE", False)

    for job_id, status in (
        ("job-done", "done"),
        ("job-error", "error"),
        ("job-running", "running"),
    ):
        job_dir = tmp_path / job_id
        job_dir.mkdir(parents=True)
        runtime.write_json_file(
            job_dir / "status.json",
            {
                "job_id": job_id,
                "status": status,
                "progress": 100 if status == "done" else 50,
            },
        )

    calls: list[str] = []

    async def _fake_reanalyze_job(job_id: str, body=None):
        calls.append(job_id)
        if job_id == "job-error":
            raise runtime.HTTPException(status_code=404, detail="source job PDF does not exist")
        return {
            "job_id": f"{job_id}-new",
            "status": "started",
            "dispatch": "local_queue",
        }

    monkeypatch.setattr(runtime, "reanalyze_job", _fake_reanalyze_job)

    payload = await runtime.reanalyze_all_jobs()

    assert payload["status"] == "started"
    assert payload["requested_count"] == 3
    assert payload["created_count"] == 1
    assert payload["skipped_count"] == 1
    assert payload["failed_count"] == 1
    assert set(calls) == {"job-done", "job-error"}
    assert payload["created"][0]["source_job_id"] == "job-done"
    assert payload["created"][0]["job_id"] == "job-done-new"
    assert payload["skipped"][0]["source_job_id"] == "job-running"
    assert payload["skipped"][0]["reason"] == "active_analysis"
    assert payload["failed"][0]["source_job_id"] == "job-error"
    assert payload["failed"][0]["status_code"] == 404


@pytest.mark.asyncio
async def test_reanalyze_all_jobs_only_selects_latest_job_per_department(tmp_path, monkeypatch):
    class _DummyOrg:
        def __init__(self, org_id: str, name: str, level: str, parent_id: str | None = None) -> None:
            self.id = org_id
            self.name = name
            self.level = level
            self.parent_id = parent_id

    class _DummyLink:
        def __init__(self, org_id: str) -> None:
            self.org_id = org_id

    class _DummyStorage:
        def __init__(self) -> None:
            self.orgs = {
                "dept-a": _DummyOrg("dept-a", "部门A", "department"),
                "unit-a": _DummyOrg("unit-a", "部门A本级", "unit", "dept-a"),
                "dept-b": _DummyOrg("dept-b", "部门B", "department"),
            }
            self.links = {
                "job-a-old": _DummyLink("unit-a"),
                "job-a-new": _DummyLink("unit-a"),
                "job-b-running": _DummyLink("dept-b"),
            }

        def get_job_org(self, job_id: str):
            return self.links.get(job_id)

        def get_by_id(self, org_id: str):
            return self.orgs.get(org_id)

    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    monkeypatch.setattr(runtime, "ORG_AVAILABLE", True)
    monkeypatch.setattr(runtime, "require_org_storage", lambda: _DummyStorage())

    timestamps = {
        "job-a-old": 1000,
        "job-a-new": 2000,
        "job-b-running": 3000,
        "job-unassigned": 4000,
    }
    for job_id, status in (
        ("job-a-old", "done"),
        ("job-a-new", "done"),
        ("job-b-running", "running"),
        ("job-unassigned", "done"),
    ):
        job_dir = tmp_path / job_id
        job_dir.mkdir(parents=True)
        status_file = job_dir / "status.json"
        runtime.write_json_file(
            status_file,
            {
                "job_id": job_id,
                "status": status,
                "progress": 100 if status == "done" else 50,
            },
        )
        os.utime(status_file, (timestamps[job_id], timestamps[job_id]))

    calls: list[str] = []

    async def _fake_reanalyze_job(job_id: str, body=None):
        calls.append(job_id)
        return {
            "job_id": f"{job_id}-new",
            "status": "started",
            "dispatch": "local_queue",
        }

    monkeypatch.setattr(runtime, "reanalyze_job", _fake_reanalyze_job)

    payload = await runtime.reanalyze_all_jobs()

    assert payload["status"] == "started"
    assert payload["latest_per_department"] is True
    assert payload["requested_count"] == 4
    assert payload["selected_count"] == 2
    assert payload["created_count"] == 1
    assert payload["failed_count"] == 0
    assert calls == ["job-a-new"]

    skipped = {(item["source_job_id"], item["reason"]) for item in payload["skipped"]}
    assert ("job-a-old", "not_latest_in_department") in skipped
    assert ("job-b-running", "active_analysis") in skipped
    assert ("job-unassigned", "unresolved_department") in skipped
    assert payload["created"][0]["source_job_id"] == "job-a-new"
    assert payload["created"][0]["department_id"] == "dept-a"


def test_resolve_latest_structured_ingest_job_prefers_latest_version_timestamp(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)

    jobs = {
        "job-old": {
            "job_id": "job-old",
            "filename": "2025-final-old.pdf",
            "organization_id": "org-1",
            "organization_name": "Org One",
            "fiscal_year": "2025",
            "report_year": 2025,
            "doc_type": "dept_final",
            "report_kind": "final",
            "version_created_at": 100.0,
            "job_created_at": 100.0,
            "status": "done",
        },
        "job-new": {
            "job_id": "job-new",
            "filename": "2025-final-new.pdf",
            "organization_id": "org-1",
            "organization_name": "Org One",
            "fiscal_year": "2025",
            "report_year": 2025,
            "doc_type": "dept_final",
            "report_kind": "final",
            "version_created_at": 200.0,
            "job_created_at": 200.0,
            "status": "uploaded",
        },
        "job-old-reanalyze": {
            "job_id": "job-old-reanalyze",
            "filename": "2025-final-old-re.pdf",
            "organization_id": "org-1",
            "organization_name": "Org One",
            "fiscal_year": "2025",
            "report_year": 2025,
            "doc_type": "dept_final",
            "report_kind": "final",
            "version_created_at": 100.0,
            "job_created_at": 300.0,
            "status": "done",
        },
    }
    for job_id, payload in jobs.items():
        job_dir = tmp_path / job_id
        job_dir.mkdir(parents=True)
        runtime.write_json_file(job_dir / "status.json", payload)

    old_result = runtime.resolve_latest_structured_ingest_job(
        "job-old-reanalyze",
        organization_id="org-1",
        organization_name="Org One",
        fiscal_year="2025",
        report_year=2025,
        doc_type="dept_final",
        report_kind="final",
        filename="2025-final-old-re.pdf",
        current_status_payload=jobs["job-old-reanalyze"],
    )
    assert old_result["is_latest"] is False
    assert old_result["reason"] == "not_latest_version"
    assert old_result["latest_job_id"] == "job-new"

    new_result = runtime.resolve_latest_structured_ingest_job(
        "job-new",
        organization_id="org-1",
        organization_name="Org One",
        fiscal_year="2025",
        report_year=2025,
        doc_type="dept_final",
        report_kind="final",
        filename="2025-final-new.pdf",
        current_status_payload=jobs["job-new"],
    )
    assert new_result["is_latest"] is True
    assert new_result["latest_job_id"] == "job-new"


@pytest.mark.asyncio
async def test_reanalyze_job_preserves_source_version_timestamp(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)

    source_dir = tmp_path / "job-source"
    source_dir.mkdir(parents=True)
    pdf_path = source_dir / "report.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    runtime.write_json_file(
        source_dir / "status.json",
        {
            "job_id": "job-source",
            "status": "done",
            "filename": "report.pdf",
            "checksum": "abc123",
            "version_created_at": 1234.5,
            "job_created_at": 1234.5,
            "organization_id": "org-1",
            "organization_name": "Org One",
            "fiscal_year": "2025",
            "doc_type": "dept_final",
            "report_year": 2025,
            "report_kind": "final",
        },
    )

    async def _fake_start_analysis(job_id: str, body=None):
        return {"job_id": job_id, "status": "started", "dispatch": "local_queue"}

    monkeypatch.setattr(runtime, "start_analysis", _fake_start_analysis)

    payload = await runtime.reanalyze_job("job-source")
    new_status = runtime.get_job_status_payload(payload["job_id"] )

    assert new_status["version_created_at"] == 1234.5
    assert float(new_status["job_created_at"]) >= 1234.5
    assert payload["source_job_id"] == "job-source"


def test_plan_structured_ingest_cleanup_filters_latest_and_shared_versions(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)

    jobs = {
        "job-old-cleanable": {
            "job_id": "job-old-cleanable",
            "filename": "org1-final-old.pdf",
            "organization_id": "org-1",
            "organization_name": "Org One",
            "fiscal_year": "2025",
            "report_year": 2025,
            "doc_type": "dept_final",
            "report_kind": "final",
            "version_created_at": 100.0,
            "job_created_at": 100.0,
            "status": "done",
        },
        "job-latest-no-structured": {
            "job_id": "job-latest-no-structured",
            "filename": "org1-final-new.pdf",
            "organization_id": "org-1",
            "organization_name": "Org One",
            "fiscal_year": "2025",
            "report_year": 2025,
            "doc_type": "dept_final",
            "report_kind": "final",
            "version_created_at": 200.0,
            "job_created_at": 200.0,
            "status": "uploaded",
        },
        "job-old-shared": {
            "job_id": "job-old-shared",
            "filename": "org2-budget-old.pdf",
            "organization_id": "org-2",
            "organization_name": "Org Two",
            "fiscal_year": "2025",
            "report_year": 2025,
            "doc_type": "dept_budget",
            "report_kind": "budget",
            "version_created_at": 100.0,
            "job_created_at": 100.0,
            "status": "done",
        },
        "job-latest-shared": {
            "job_id": "job-latest-shared",
            "filename": "org2-budget-reanalyze.pdf",
            "organization_id": "org-2",
            "organization_name": "Org Two",
            "fiscal_year": "2025",
            "report_year": 2025,
            "doc_type": "dept_budget",
            "report_kind": "budget",
            "version_created_at": 100.0,
            "job_created_at": 300.0,
            "status": "done",
        },
    }
    for job_id, payload in jobs.items():
        job_dir = tmp_path / job_id
        job_dir.mkdir(parents=True)
        runtime.write_json_file(job_dir / "status.json", payload)

    runtime.write_structured_ingest_payload(
        tmp_path / "job-old-cleanable",
        {
            "job_id": "job-old-cleanable",
            "status": "done",
            "document_version_id": 11,
        },
    )
    runtime.write_structured_ingest_payload(
        tmp_path / "job-old-shared",
        {
            "job_id": "job-old-shared",
            "status": "done",
            "document_version_id": 22,
        },
    )
    runtime.write_structured_ingest_payload(
        tmp_path / "job-latest-shared",
        {
            "job_id": "job-latest-shared",
            "status": "done",
            "document_version_id": 22,
        },
    )

    plan = runtime.plan_structured_ingest_cleanup()

    assert plan["scope_count"] == 2
    assert plan["cleanup_document_version_count"] == 1
    assert plan["cleanup_job_count"] == 1
    assert plan["cleanup_document_versions"][0]["document_version_id"] == 11
    assert plan["cleanup_jobs"][0]["job_id"] == "job-old-cleanable"
    assert plan["blocked_document_version_count"] == 1
    assert plan["blocked_document_versions"][0]["document_version_id"] == 22
    skipped_reasons = {
        (item.get("job_id"), item.get("reason"))
        for item in plan["skipped_jobs"]
    }
    assert ("job-old-shared", "shared_with_latest_job") in skipped_reasons


@pytest.mark.asyncio
async def test_cleanup_structured_ingest_history_marks_old_jobs_cleaned(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)

    old_job_dir = tmp_path / "job-old"
    old_job_dir.mkdir(parents=True)
    runtime.write_json_file(
        old_job_dir / "status.json",
        {
            "job_id": "job-old",
            "filename": "history.pdf",
            "organization_id": "org-1",
            "organization_name": "Org One",
            "fiscal_year": "2025",
            "report_year": 2025,
            "doc_type": "dept_final",
            "report_kind": "final",
            "version_created_at": 100.0,
            "job_created_at": 100.0,
            "status": "done",
        },
    )
    runtime.write_structured_ingest_payload(
        old_job_dir,
        {
            "job_id": "job-old",
            "status": "done",
            "document_version_id": 31,
            "ps_sync": {"report_id": "report-31", "line_item_count": 12},
        },
    )

    latest_job_dir = tmp_path / "job-latest"
    latest_job_dir.mkdir(parents=True)
    runtime.write_json_file(
        latest_job_dir / "status.json",
        {
            "job_id": "job-latest",
            "filename": "latest.pdf",
            "organization_id": "org-1",
            "organization_name": "Org One",
            "fiscal_year": "2025",
            "report_year": 2025,
            "doc_type": "dept_final",
            "report_kind": "final",
            "version_created_at": 200.0,
            "job_created_at": 200.0,
            "status": "uploaded",
        },
    )

    from src.db.connection import DatabaseConnection

    class _FakeTransaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _FakeConnection:
        def __init__(self) -> None:
            self.deleted: list[int] = []

        def transaction(self):
            return _FakeTransaction()

        async def execute(self, query: str, document_version_id: int):
            assert "DELETE FROM fiscal_document_versions" in query
            self.deleted.append(document_version_id)
            return "DELETE 1"

    fake_conn = _FakeConnection()

    async def _fake_acquire():
        return fake_conn

    released: list[object] = []

    async def _fake_release(conn):
        released.append(conn)

    monkeypatch.setattr(DatabaseConnection, "acquire", _fake_acquire)
    monkeypatch.setattr(DatabaseConnection, "release", _fake_release)

    payload = await runtime.cleanup_structured_ingest_history({"dry_run": False})

    assert payload["status"] == "done"
    assert payload["deleted_document_version_ids"] == [31]
    assert payload["updated_job_ids"] == ["job-old"]
    assert fake_conn.deleted == [31]
    assert released == [fake_conn]

    structured = runtime.get_job_review_payload("job-old")
    assert structured["status"] == "cleaned"
    assert structured["reason"] == "historical_version_cleaned"
    assert structured["document_version_id"] is None
    assert structured["cleaned_document_version_id"] == 31
    assert structured["latest_job_id"] == "job-latest"
    assert structured["latest_filename"] == "latest.pdf"
    assert structured["ps_sync"]["report_id"] is None


def test_ignore_job_issue_filters_ai_findings_and_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)

    def _issue(issue_id: str, source: str, severity: str, title: str) -> dict:
        return {
            "id": issue_id,
            "source": source,
            "severity": severity,
            "title": title,
            "message": title,
            "evidence": [{"page": 1, "text": title}],
            "location": {"page": 1, "section": "", "table": "", "row": "", "col": ""},
            "metrics": {},
            "tags": [],
            "created_at": 1.0,
        }

    ai_issue_1 = _issue("ai:keep-me-out", "ai", "medium", "AI false positive")
    ai_issue_2 = _issue("ai:keep-me", "ai", "low", "AI valid issue")
    rule_issue = _issue("rule:still-here", "rule", "high", "Rule issue")

    job_dir = tmp_path / "job-ignore"
    job_dir.mkdir(parents=True)
    runtime.write_json_file(
        job_dir / "status.json",
        {
            "job_id": "job-ignore",
            "status": "done",
            "progress": 100,
            "filename": "job-ignore.pdf",
            "result": {
                "ai_findings": [ai_issue_1, ai_issue_2],
                "rule_findings": [rule_issue],
                "merged": {
                    "totals": {
                        "ai": 2,
                        "rule": 1,
                        "merged": 3,
                        "conflicts": 0,
                        "agreements": 0,
                    },
                    "conflicts": [],
                    "agreements": [],
                },
                "issues": {
                    "error": [rule_issue],
                    "warn": [],
                    "info": [],
                    "all": [rule_issue],
                },
                "meta": {},
            },
        },
    )

    payload = runtime.ignore_job_issue("job-ignore", "ai:keep-me-out")

    assert payload["ignored_issue_id"] == "ai:keep-me-out"
    assert payload["ignored_issue_ids"] == ["ai:keep-me-out"]
    assert [item["id"] for item in payload["result"]["ai_findings"]] == ["ai:keep-me"]
    assert payload["result"]["merged"]["totals"]["ai"] == 1
    assert payload["result"]["merged"]["totals"]["merged"] == 2

    summary = runtime.collect_job_summary(job_dir)
    assert summary["merged_issue_total"] == 2
    assert summary["ai_issue_total"] == 1
    assert summary["ai_issue_warn"] == 1
    assert summary["issue_total"] == 1
