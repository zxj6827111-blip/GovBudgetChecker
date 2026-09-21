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
    persisted_payloads = []

    async def _capture_persist(payload):
        persisted_payloads.append(dict(payload))
        return True

    monkeypatch.setattr(runtime, "persist_analysis_job_snapshot", _capture_persist)

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
    assert status["storage_key"] == f"{payload['job_id']}/sample_final_2025.pdf"
    assert status["storage_backend"] == "filesystem"
    assert status["content_type"] == "application/pdf"
    assert float(status["version_created_at"]) > 0
    assert float(status["job_created_at"]) > 0
    assert len(persisted_payloads) == 1
    assert persisted_payloads[0]["job_id"] == payload["job_id"]
    assert persisted_payloads[0]["storage_key"] == status["storage_key"]


@pytest.mark.asyncio
async def test_store_upload_file_can_delay_database_snapshot_until_deduplication(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    persisted_payloads = []

    async def _capture_persist(payload):
        persisted_payloads.append(payload)
        return True

    monkeypatch.setattr(runtime, "persist_analysis_job_snapshot", _capture_persist)
    upload = UploadFile(
        filename="deferred.pdf",
        file=io.BytesIO(b"%PDF-1.4\n1 0 obj << /Type /Catalog >>\n%%EOF"),
    )

    payload = await runtime.store_upload_file(upload, persist_snapshot=False)

    assert persisted_payloads == []
    assert (tmp_path / payload["job_id"] / "status.json").is_file()


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
    (job_dir / runtime.PERSISTENCE_STATE_FILENAME).write_text(
        json.dumps(
            {
                "status": "pending_retry",
                "last_attempt_ts": 123.0,
                "error": "db down",
            }
        ),
        encoding="utf-8",
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
    assert summary["persistence_status"] == "pending_retry"
    assert summary["persistence_last_attempt_ts"] == 123.0
    assert summary["persistence_error"] == "db down"


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

    persisted: list[dict] = []

    async def _fake_persist(payload: dict, *, include_results: bool = False) -> bool:
        persisted.append({"payload": dict(payload), "include_results": include_results})
        return True

    queue = _DummyQueue()
    monkeypatch.setattr(runtime, "_pipeline_runner", _dummy_runner)
    monkeypatch.setattr(runtime, "_job_queue", queue)
    monkeypatch.setattr(runtime, "persist_analysis_job_snapshot", _fake_persist)

    payload = await runtime.start_analysis("job-queued", {"mode": "legacy"})

    assert payload["status"] == "started"
    assert queue.enqueued == ["job-queued"]
    assert persisted
    assert persisted[0]["payload"]["job_id"] == "job-queued"
    assert persisted[0]["payload"]["status"] == "queued"
    assert persisted[0]["include_results"] is False

    status = runtime.read_json_file(job_dir / "status.json", default={})
    assert status["organization_id"] == "org-1"
    assert status["organization_name"] == "测试单位"
    assert status["organization_match_type"] == "auto"
    assert status["organization_match_confidence"] == 0.88
    assert status["checksum"] == "abc123"


@pytest.mark.asyncio
async def test_start_analysis_keeps_requested_year_and_records_filename_conflict(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    job_dir = tmp_path / "job-year-conflict"
    job_dir.mkdir()
    (job_dir / "2025执行数与2026预算.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    runtime.write_json_file(
        job_dir / "status.json",
        {"job_id": "job-year-conflict", "status": "uploaded"},
    )

    async def _dummy_runner(_job_dir):
        return None

    class _DummyQueue:
        async def enqueue(self, _job_id: str) -> None:
            return None

    monkeypatch.setattr(runtime, "_pipeline_runner", _dummy_runner)
    monkeypatch.setattr(runtime, "_job_queue", _DummyQueue())

    await runtime.start_analysis(
        "job-year-conflict",
        {"report_year": 2026, "fiscal_year": "2026", "doc_type": "dept_budget"},
    )

    status = runtime.read_json_file(job_dir / "status.json", default={})
    assert status["report_year"] == 2026
    assert status["report_year_source"] == "request"
    assert status["year_conflict"] == {
        "selected_year": 2026,
        "filename_year": 2025,
        "source": "request",
        "requires_manual_review": True,
    }


@pytest.mark.asyncio
async def test_reanalyze_job_refreshes_same_job_and_reuses_analysis_context(tmp_path, monkeypatch):
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

    async def _dummy_runner(_job_dir):
        return None

    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    monkeypatch.setattr(runtime, "ORG_AVAILABLE", True)
    monkeypatch.setattr(runtime, "require_org_storage", lambda: _DummyStorage())
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
            # 请求契约（P0）：structured/legacy 模式不请求 AI，旧值 true 会被 422
            "use_ai_assist": False,
            "mode": "structured",
        },
    )

    payload = await runtime.reanalyze_job("job-source")

    assert payload["status"] == "started"
    assert payload["source_job_id"] == "job-source"
    assert payload["job_id"] == "job-source"
    assert queue.enqueued == ["job-source"]

    status = runtime.read_json_file(source_job_dir / "status.json", default={})
    assert status["status"] == "queued"
    assert status["filename"] == "source_2025.pdf"
    assert status["checksum"] == "checksum-source"
    assert status["organization_id"] == "org-1"
    assert status["fiscal_year"] == "2025"
    assert status["doc_type"] == "dept_budget"
    assert status["report_year"] == 2025
    assert status["use_local_rules"] is False
    assert status["use_ai_assist"] is False
    assert status["mode"] == "structured"


@pytest.mark.asyncio
async def test_reanalyze_job_clears_ephemeral_outputs_before_refresh(tmp_path, monkeypatch):
    class _DummyQueue:
        def __init__(self) -> None:
            self.enqueued: list[str] = []

        async def enqueue(self, job_id: str) -> None:
            self.enqueued.append(job_id)

    async def _dummy_runner(_job_dir):
        return None

    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    monkeypatch.setattr(runtime, "ORG_AVAILABLE", False)
    monkeypatch.setattr(runtime, "_pipeline_runner", _dummy_runner)
    monkeypatch.setattr(runtime, "_job_queue", _DummyQueue())

    job_dir = tmp_path / "job-source"
    job_dir.mkdir(parents=True)
    (job_dir / "source.pdf").write_bytes(
        b"%PDF-1.4\n1 0 obj <<>> endobj\ntrailer <<>>\n%%EOF\n"
    )
    runtime.write_json_file(
        job_dir / "status.json",
        {
            "job_id": "job-source",
            "status": "done",
            "progress": 100,
            "filename": "source.pdf",
        },
    )
    for filename in (
        "structured_ingest.json",
        "ignored_issues.json",
        "annotated.pdf",
        "compare_old_vs_new.json",
    ):
        (job_dir / filename).write_text("stale", encoding="utf-8")

    await runtime.reanalyze_job("job-source")

    for filename in (
        "structured_ingest.json",
        "ignored_issues.json",
        "annotated.pdf",
        "compare_old_vs_new.json",
    ):
        assert not (job_dir / filename).exists()


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
        return {"job_id": job_id, "status": "started", "dispatch": "local_queue"}

    monkeypatch.setattr(runtime, "reanalyze_job", _fake_reanalyze_job)

    payload = await runtime.reanalyze_all_jobs()

    assert payload["status"] == "started"
    assert payload["requested_count"] == 3
    assert payload["created_count"] == 1
    assert payload["skipped_count"] == 1
    assert payload["failed_count"] == 1
    assert set(calls) == {"job-done", "job-error"}
    assert payload["created"][0]["source_job_id"] == "job-done"
    assert payload["created"][0]["job_id"] == "job-done"
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
        return {"job_id": job_id, "status": "started", "dispatch": "local_queue"}

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
    assert ("job-a-old", "not_latest_in_scope") in skipped
    assert ("job-b-running", "active_analysis") in skipped
    assert ("job-unassigned", "unresolved_department") in skipped
    assert payload["created"][0]["source_job_id"] == "job-a-new"
    assert payload["created"][0]["department_id"] == "dept-a"
    assert payload["created"][0]["scope_id"] == "unit-a"
    assert payload["created"][0]["scope_level"] == "unit"


@pytest.mark.asyncio
async def test_reanalyze_all_jobs_selects_latest_department_and_unit_reports(
    tmp_path, monkeypatch
):
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
                "unit-a": _DummyOrg("unit-a", "部门A下属单位", "unit", "dept-a"),
            }
            self.links = {
                "job-dept": _DummyLink("dept-a"),
                "job-unit-latest": _DummyLink("unit-a"),
            }

        def get_job_org(self, job_id: str):
            return self.links.get(job_id)

        def get_by_id(self, org_id: str):
            return self.orgs.get(org_id)

    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    monkeypatch.setattr(runtime, "ORG_AVAILABLE", True)
    monkeypatch.setattr(runtime, "require_org_storage", lambda: _DummyStorage())

    timestamps = {
        "job-dept": 1000,
        "job-unit-latest": 2000,
    }
    for job_id in timestamps:
        job_dir = tmp_path / job_id
        job_dir.mkdir(parents=True)
        status_file = job_dir / "status.json"
        runtime.write_json_file(
            status_file,
            {
                "job_id": job_id,
                "status": "done",
                "progress": 100,
                "organization_id": "dept-a" if job_id == "job-dept" else "unit-a",
                "organization_name": "部门A" if job_id == "job-dept" else "部门A下属单位",
                "organization_match_type": "manual",
                "organization_match_confidence": 1.0,
            },
        )
        os.utime(status_file, (timestamps[job_id], timestamps[job_id]))

    calls: list[str] = []

    async def _fake_reanalyze_job(job_id: str, body=None):
        calls.append(job_id)
        return {"job_id": job_id, "status": "started", "dispatch": "local_queue"}

    monkeypatch.setattr(runtime, "reanalyze_job", _fake_reanalyze_job)

    payload = await runtime.reanalyze_all_jobs({"latest_per_department": True})

    assert payload["status"] == "started"
    assert payload["requested_count"] == 2
    assert payload["selected_count"] == 2
    assert payload["created_count"] == 2
    assert calls == ["job-unit-latest", "job-dept"]

    created_pairs = {
        (item["source_job_id"], item["scope_id"], item["scope_level"])
        for item in payload["created"]
    }
    assert ("job-dept", "dept-a", "department") in created_pairs
    assert ("job-unit-latest", "unit-a", "unit") in created_pairs


@pytest.mark.asyncio
async def test_reanalyze_all_jobs_can_skip_subordinate_unit_reports_for_department_mode(
    tmp_path, monkeypatch
):
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
                "unit-a": _DummyOrg("unit-a", "部门A下属单位", "unit", "dept-a"),
            }
            self.links = {
                "job-dept": _DummyLink("dept-a"),
                "job-unit-latest": _DummyLink("unit-a"),
            }

        def get_job_org(self, job_id: str):
            return self.links.get(job_id)

        def get_by_id(self, org_id: str):
            return self.orgs.get(org_id)

    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    monkeypatch.setattr(runtime, "ORG_AVAILABLE", True)
    monkeypatch.setattr(runtime, "require_org_storage", lambda: _DummyStorage())

    timestamps = {
        "job-dept": 1000,
        "job-unit-latest": 2000,
    }
    for job_id in timestamps:
        job_dir = tmp_path / job_id
        job_dir.mkdir(parents=True)
        status_file = job_dir / "status.json"
        runtime.write_json_file(
            status_file,
            {
                "job_id": job_id,
                "status": "done",
                "progress": 100,
                "organization_id": "dept-a" if job_id == "job-dept" else "unit-a",
                "organization_name": "部门A" if job_id == "job-dept" else "部门A下属单位",
                "organization_match_type": "manual",
                "organization_match_confidence": 1.0,
            },
        )
        os.utime(status_file, (timestamps[job_id], timestamps[job_id]))

    calls: list[str] = []

    async def _fake_reanalyze_job(job_id: str, body=None):
        calls.append(job_id)
        return {"job_id": job_id, "status": "started", "dispatch": "local_queue"}

    monkeypatch.setattr(runtime, "reanalyze_job", _fake_reanalyze_job)

    payload = await runtime.reanalyze_all_jobs(
        {
            "latest_per_department": True,
            "direct_department_only": True,
        }
    )

    assert payload["status"] == "started"
    assert payload["direct_department_only"] is True
    assert payload["requested_count"] == 2
    assert payload["selected_count"] == 1
    assert payload["created_count"] == 1
    assert calls == ["job-dept"]

    skipped = {(item["source_job_id"], item["reason"]) for item in payload["skipped"]}
    assert ("job-unit-latest", "subordinate_unit_report") in skipped
    assert payload["created"][0]["source_job_id"] == "job-dept"


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
    new_status = runtime.get_job_status_payload(payload["job_id"])

    assert new_status["version_created_at"] == 1234.5
    assert new_status["job_created_at"] == 1234.5
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

        async def fetch(self, query: str, version_ids) -> list[dict]:
            # 计划阶段会查一次 Material Slot 绑定；本用例没有绑定版本。
            assert "FROM fiscal_document_versions" in query
            assert "slot_id IS NOT NULL" in query
            return []

        async def execute(self, query: str, document_version_id: int):
            assert "DELETE FROM fiscal_document_versions" in query
            # 执行阶段必须自带 slot_id 守卫，这是 TOCTOU 的第二道闸
            assert "slot_id IS NULL" in query
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


# ---------------------------------------------------------------------------
# structured-ingest cleanup × Material Ledger
#
# 从 PR #42 起 `fiscal_document_versions.slot_id` 把 PDF 文件版本绑到了
# `material_slots`。旧的结构化清理是按"每个 scope 只留最新入库版本"设计的，
# 它看到的是入库维度的冗余，看不到台账维度的归属——因此必须显式保护：
# 已绑定即不可由本链路删除，无论是不是当前版本。
# ---------------------------------------------------------------------------

MATERIAL_SLOT_BOUND = "material_slot_bound"


class _CleanupFakeConnection:
    """清理链路用的记录式假连接。

    只实现链路真正会用到的两条语句，不模拟 Postgres。

    ``slot_bound`` 模拟"计划阶段就能查到已绑定"；
    ``bound_after_plan`` 模拟"计划生成之后、DELETE 之前才发生绑定"——
    此时 DELETE 因为带 ``slot_id IS NULL`` 守卫而影响 0 行。
    """

    def __init__(self, *, slot_bound=None, bound_after_plan=None) -> None:
        self.slot_bound = dict(slot_bound or {})
        self.bound_after_plan = set(bound_after_plan or ())
        self.fetch_calls: list = []
        self.delete_attempts: list = []
        self.deleted: list = []

    def transaction(self):
        class _Transaction:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *_exc):
                return False

        return _Transaction()

    async def fetch(self, query: str, version_ids) -> list:
        assert "FROM fiscal_document_versions" in query
        assert "slot_id IS NOT NULL" in query
        self.fetch_calls.append(list(version_ids))
        return [
            {"id": version_id, "slot_id": self.slot_bound[version_id]}
            for version_id in version_ids
            if version_id in self.slot_bound
        ]

    async def execute(self, query: str, document_version_id: int) -> str:
        assert "DELETE FROM fiscal_document_versions" in query
        # 执行阶段的第二道闸：不依赖计划是否准确，绑定版本一律删不掉
        assert "slot_id IS NULL" in query
        self.delete_attempts.append((query, document_version_id))
        if document_version_id in self.bound_after_plan:
            return "DELETE 0"
        self.deleted.append(document_version_id)
        return "DELETE 1"


def _patch_cleanup_db(monkeypatch, fake):
    from src.db.connection import DatabaseConnection

    released: list = []

    async def _acquire():
        return fake

    async def _release(conn):
        released.append(conn)

    monkeypatch.setattr(DatabaseConnection, "acquire", _acquire)
    monkeypatch.setattr(DatabaseConnection, "release", _release)
    return released


def _write_cleanup_job(
    root,
    job_id: str,
    *,
    document_version_id,
    version_created_at: float,
    job_created_at: float,
    organization_id: str = "org-1",
    organization_name: str = "Org One",
    doc_type: str = "dept_final",
    report_kind: str = "final",
) -> None:
    job_dir = root / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    runtime.write_json_file(
        job_dir / "status.json",
        {
            "job_id": job_id,
            "filename": f"{job_id}.pdf",
            "organization_id": organization_id,
            "organization_name": organization_name,
            "fiscal_year": "2025",
            "report_year": 2025,
            "doc_type": doc_type,
            "report_kind": report_kind,
            "version_created_at": version_created_at,
            "job_created_at": job_created_at,
            "status": "done",
        },
    )
    if document_version_id is not None:
        runtime.write_structured_ingest_payload(
            job_dir,
            {
                "job_id": job_id,
                "status": "done",
                "document_version_id": document_version_id,
                "ps_sync": {"report_id": f"report-{document_version_id}"},
            },
        )


def _structured_payload(job_id: str) -> dict:
    return runtime.get_job_review_payload(job_id) or {}


# ---- 纯计划阶段的保护规则 ---------------------------------------------------


def test_pure_plan_is_marked_as_not_material_ledger_checked(tmp_path, monkeypatch):
    """纯计划不知道台账绑定，必须自报"没校验过"，不能冒充可执行计划。"""
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)

    plan = runtime.plan_structured_ingest_cleanup()

    assert plan["material_ledger_checked"] is False


def test_material_ledger_protection_moves_bound_versions_to_blocked():
    plan = {
        "cleanup_document_versions": [
            {"document_version_id": 1, "jobs": [{"job_id": "job-a"}]},
            {"document_version_id": 2, "jobs": [{"job_id": "job-b"}]},
        ],
        "cleanup_jobs": [
            {"job_id": "job-a", "document_version_id": 1},
            {"job_id": "job-b", "document_version_id": 2},
        ],
        "blocked_document_versions": [],
        "skipped_jobs": [],
        "cleanup_document_version_count": 2,
        "cleanup_job_count": 2,
        "blocked_document_version_count": 0,
        "skipped_job_count": 0,
    }

    result = runtime._apply_material_ledger_protection(plan, {1: "slot-abc"})

    assert [item["document_version_id"] for item in result["cleanup_document_versions"]] == [2]
    blocked = result["blocked_document_versions"]
    assert len(blocked) == 1
    assert blocked[0]["document_version_id"] == 1
    assert blocked[0]["reason"] == MATERIAL_SLOT_BOUND
    # slot_id 一并透出，运维不必再查库
    assert blocked[0]["slot_id"] == "slot-abc"
    # 涉及的历史任务从待清理里摘掉，并给出跳过原因
    assert [job["job_id"] for job in result["cleanup_jobs"]] == ["job-b"]
    assert {
        (job["job_id"], job["reason"]) for job in result["skipped_jobs"]
    } == {("job-a", MATERIAL_SLOT_BOUND)}
    assert result["cleanup_document_version_count"] == 1
    assert result["blocked_document_version_count"] == 1
    assert result["material_ledger_checked"] is True


def test_material_ledger_protection_leaves_unbound_plan_untouched():
    plan = {
        "cleanup_document_versions": [{"document_version_id": 7, "jobs": []}],
        "cleanup_jobs": [{"job_id": "job-a", "document_version_id": 7}],
        "blocked_document_versions": [],
        "skipped_jobs": [],
    }
    result = runtime._apply_material_ledger_protection(plan, {})
    assert result["cleanup_document_versions"] == plan["cleanup_document_versions"]
    assert result["blocked_document_versions"] == []
    assert result["material_ledger_checked"] is True


# ---- Case 1：槽位「当前版本」不得删除 --------------------------------------


@pytest.mark.asyncio
async def test_cleanup_keeps_slot_current_version(tmp_path, monkeypatch):
    """版本 V1 是槽位 A 的当前版本，旧清理链路认为它是历史版本也必须放过。"""
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)

    _write_cleanup_job(
        tmp_path, "job-old", document_version_id=41, version_created_at=100.0, job_created_at=100.0
    )
    _write_cleanup_job(
        tmp_path, "job-new", document_version_id=42, version_created_at=200.0, job_created_at=200.0
    )

    fake = _CleanupFakeConnection(slot_bound={41: "slot-A"})
    _patch_cleanup_db(monkeypatch, fake)

    result = await runtime.cleanup_structured_ingest_history({"dry_run": False})

    assert fake.deleted == []
    assert fake.delete_attempts == [], "计划阶段就应拦下，不该走到 DELETE"
    assert result["deleted_document_version_ids"] == []
    assert result["blocked_at_delete"] == []
    blocked_ids = {item["document_version_id"] for item in result["blocked_document_versions"]}
    assert 41 in blocked_ids
    bound_entry = next(
        item for item in result["blocked_document_versions"] if item["document_version_id"] == 41
    )
    assert bound_entry["reason"] == MATERIAL_SLOT_BOUND
    assert bound_entry["slot_id"] == "slot-A"

    # job 侧不得被标成已清理：数据库里那一版还在
    assert result["updated_job_ids"] == []
    payload = _structured_payload("job-old")
    assert payload["status"] == "done"
    assert payload["document_version_id"] == 41


# ---- Case 2：槽位「历史版本」也不得删除 ------------------------------------


@pytest.mark.asyncio
async def test_cleanup_keeps_slot_historical_version(tmp_path, monkeypatch):
    """V1 与 V2 同属槽位 A；V2 是当前版本，V1 是历史版本——V1 同样不许删。"""
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)

    _write_cleanup_job(
        tmp_path, "job-v1", document_version_id=51, version_created_at=100.0, job_created_at=100.0
    )
    _write_cleanup_job(
        tmp_path, "job-v2", document_version_id=52, version_created_at=200.0, job_created_at=200.0
    )

    fake = _CleanupFakeConnection(slot_bound={51: "slot-A", 52: "slot-A"})
    _patch_cleanup_db(monkeypatch, fake)

    result = await runtime.cleanup_structured_ingest_history({"dry_run": False})

    assert fake.deleted == []
    assert result["deleted_document_version_ids"] == []
    assert 51 in {item["document_version_id"] for item in result["blocked_document_versions"]}
    # "不是当前版本"从来不是可以删的理由
    assert _structured_payload("job-v1")["document_version_id"] == 51


# ---- Case 3：两个占位槽位不被旧 scope 误清理 -------------------------------


@pytest.mark.asyncio
async def test_cleanup_keeps_two_placeholder_slot_versions(tmp_path, monkeypatch):
    """两个 mapping_required 占位槽位（同组织/同年度/同文种、不同校验和）。

    旧 structured-ingest scope 只看"组织+年度+文种"，会把这两个任务归进
    同一个分组并认为旧的那份是冗余版本；但它们在台账里是两条独立的材料
    （身份未确认时按文档校验和各自成槽），任何一份都不许删。
    """
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)

    _write_cleanup_job(
        tmp_path, "job-doc-a", document_version_id=61, version_created_at=100.0, job_created_at=100.0
    )
    _write_cleanup_job(
        tmp_path, "job-doc-b", document_version_id=62, version_created_at=200.0, job_created_at=200.0
    )

    # 计划阶段：两个任务确实被归进同一个 scope（否则本用例没有意义）
    plan = runtime.plan_structured_ingest_cleanup()
    assert plan["scope_count"] == 1
    assert plan["cleanup_document_version_count"] == 1
    assert plan["cleanup_document_versions"][0]["document_version_id"] == 61

    fake = _CleanupFakeConnection(
        slot_bound={61: "slot-placeholder-a", 62: "slot-placeholder-b"}
    )
    _patch_cleanup_db(monkeypatch, fake)

    result = await runtime.cleanup_structured_ingest_history({"dry_run": False})

    assert fake.deleted == []
    assert result["deleted_document_version_ids"] == []
    assert 61 in {item["document_version_id"] for item in result["blocked_document_versions"]}
    assert "slot-placeholder-a" in {
        item.get("slot_id") for item in result["blocked_document_versions"]
    }
    assert _structured_payload("job-doc-a")["document_version_id"] == 61


# ---- Case 4：plan 之后才绑定（TOCTOU） -------------------------------------


@pytest.mark.asyncio
async def test_cleanup_delete_guard_blocks_binding_after_plan(tmp_path, monkeypatch):
    """计划时未绑定、执行前被绑定：DELETE 影响 0 行，按阻断处理。

    这条用例专门证明执行阶段的 ``slot_id IS NULL`` 守卫真的有效——
    只信计划的话，这一版会被删掉，而它已经进了台账。
    """
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)

    _write_cleanup_job(
        tmp_path, "job-old", document_version_id=71, version_created_at=100.0, job_created_at=100.0
    )
    _write_cleanup_job(
        tmp_path, "job-new", document_version_id=72, version_created_at=200.0, job_created_at=200.0
    )

    # 计划阶段查不到绑定；DELETE 时该版本已被另一个事务绑定 → 影响 0 行
    fake = _CleanupFakeConnection(slot_bound={}, bound_after_plan={71})
    _patch_cleanup_db(monkeypatch, fake)

    result = await runtime.cleanup_structured_ingest_history({"dry_run": False})

    assert [attempt[1] for attempt in fake.delete_attempts] == [71], "必须真的尝试过删除"
    assert fake.deleted == []
    assert result["deleted_document_version_ids"] == []
    assert result["deleted_document_version_count"] == 0
    assert result["blocked_at_delete"] == [
        {"document_version_id": 71, "reason": MATERIAL_SLOT_BOUND}
    ]
    assert result["blocked_at_delete_count"] == 1

    # 双真相防护：数据库里还在，文件系统就不许说已清理
    assert result["updated_job_ids"] == []
    payload = _structured_payload("job-old")
    assert payload["status"] == "done"
    assert payload["document_version_id"] == 71


# ---- §9：未绑定的历史版本仍按原行为清理 ------------------------------------


@pytest.mark.asyncio
async def test_cleanup_still_deletes_unbound_history_next_to_bound_version(
    tmp_path, monkeypatch
):
    """清理没有被整体禁掉：同一轮里，绑定的拦住、未绑定的照常删。

    组织不同 → 两个 scope；org-1 的旧版本已绑定台账，org-2 的旧版本没有。
    """
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)

    _write_cleanup_job(
        tmp_path, "job-bound-old", document_version_id=81, version_created_at=100.0, job_created_at=100.0
    )
    _write_cleanup_job(
        tmp_path, "job-bound-new", document_version_id=82, version_created_at=200.0, job_created_at=200.0
    )
    _write_cleanup_job(
        tmp_path,
        "job-free-old",
        document_version_id=83,
        version_created_at=100.0,
        job_created_at=100.0,
        organization_id="org-2",
        organization_name="Org Two",
    )
    _write_cleanup_job(
        tmp_path,
        "job-free-new",
        document_version_id=84,
        version_created_at=200.0,
        job_created_at=200.0,
        organization_id="org-2",
        organization_name="Org Two",
    )

    fake = _CleanupFakeConnection(slot_bound={81: "slot-A"})
    _patch_cleanup_db(monkeypatch, fake)

    result = await runtime.cleanup_structured_ingest_history({"dry_run": False})

    assert fake.deleted == [83]
    assert result["deleted_document_version_ids"] == [83]
    assert result["updated_job_ids"] == ["job-free-old"]
    # 未绑定的那份被正常清理，job 侧写 cleaned
    cleaned = _structured_payload("job-free-old")
    assert cleaned["status"] == "cleaned"
    assert cleaned["reason"] == "historical_version_cleaned"
    assert cleaned["document_version_id"] is None
    # 绑定的那份原样保留
    assert _structured_payload("job-bound-old")["document_version_id"] == 81


@pytest.mark.asyncio
async def test_cleanup_dry_run_reports_material_ledger_protection(tmp_path, monkeypatch):
    """预览也必须过台账校验，否则它会把不该删的算进"将删除"。"""
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)

    _write_cleanup_job(
        tmp_path, "job-old", document_version_id=91, version_created_at=100.0, job_created_at=100.0
    )
    _write_cleanup_job(
        tmp_path, "job-new", document_version_id=92, version_created_at=200.0, job_created_at=200.0
    )

    fake = _CleanupFakeConnection(slot_bound={91: "slot-A"})
    _patch_cleanup_db(monkeypatch, fake)

    preview = await runtime.cleanup_structured_ingest_history({"dry_run": True})

    assert preview["dry_run"] is True
    assert preview["material_ledger_checked"] is True
    assert preview["cleanup_document_versions"] == []
    assert preview["cleanup_document_version_count"] == 0
    assert 91 in {item["document_version_id"] for item in preview["blocked_document_versions"]}
    assert fake.deleted == [], "预览不得删除任何东西"


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
            "quality_status": "degraded",
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
    assert summary["quality_status"] == "degraded"


def test_get_job_status_payload_lifts_dual_mode_result_to_top_level(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)

    ai_issue = {
        "id": "ai:1",
        "source": "ai",
        "severity": "medium",
        "title": "AI issue",
        "message": "AI issue",
        "evidence": [{"page": 1, "text": "AI issue"}],
        "location": {"page": 1},
        "metrics": {},
        "tags": [],
        "created_at": 1.0,
    }
    rule_issue = {
        "id": "rule:1",
        "source": "rule",
        "severity": "high",
        "title": "Rule issue",
        "message": "Rule issue",
        "evidence": [{"page": 2, "text": "Rule issue"}],
        "location": {"page": 2},
        "metrics": {},
        "tags": [],
        "created_at": 1.0,
    }

    job_dir = tmp_path / "job-dual"
    job_dir.mkdir(parents=True)
    runtime.write_json_file(
        job_dir / "status.json",
        {
            "job_id": "job-dual",
            "status": "done",
            "progress": 100,
            "mode": "dual",
            "dual_mode_enabled": True,
            "ai_findings": [],
            "rule_findings": [],
            "summary": None,
            "meta": None,
            "result": {
                "summary": {"merged_issue_total": 2},
                "meta": {"elapsed_ms": {"ai": 1000, "rule": 200}},
                "ai_findings": [ai_issue],
                "rule_findings": [rule_issue],
                "merged": {
                    "totals": {
                        "ai": 1,
                        "rule": 1,
                        "merged": 2,
                        "conflicts": 0,
                        "agreements": 0,
                    },
                    "conflicts": [],
                    "agreements": [],
                },
            },
        },
    )

    payload = runtime.get_job_status_payload("job-dual")

    assert [item["id"] for item in payload["ai_findings"]] == ["ai:1"]
    assert [item["id"] for item in payload["rule_findings"]] == ["rule:1"]
    assert payload["summary"]["merged_issue_total"] == 2
    assert payload["meta"]["elapsed_ms"]["ai"] == 1000
