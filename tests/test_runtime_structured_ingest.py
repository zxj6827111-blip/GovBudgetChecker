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
