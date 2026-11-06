"""补核导出必须保存会话身份、隔离历史版本，且不得把人工处理改成自动检查通过。"""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import csv
import io
import json
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET
import zipfile

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from api import material_access, runtime
from api.main import app
from api.routes import reports
from src.services import analysis_fact_snapshot, review_lifecycle_service
from src.services.material_detail_query_service import MaterialDetailQueryService


def _review():
    return {
        "slot_id": "slot-1",
        "current_analysis": {"job_uuid": "job-1", "analysis_revision": 3,
                             "analysis_basis_token": "job-1:3", "document_version_id": 7},
        "current_session": {"review_session_id": "session-1", "analysis_job_uuid": "job-1",
                            "analysis_basis_token": "job-1:3", "document_version_id": 7, "status": "in_progress"},
        "history": [],
        "obligation_review": {"available": True, "reason": None, "pending_total": 1, "covered_total": 1,
                              "items": [{"obligation_id": "OBL-T2", "title": "收入表合计",
                                         "decision": "verified_issue", "decision_revision": 2,
                                         "decided_by": "reviewer", "decided_at": "2026-11-05T10:00:00Z",
                                         "note": "第7–8页总分不平，已核对原件", "evidence_reference": "PDF 7–8",
                                         "locations": [{"page": 7}]},
                                        {"obligation_id": "OBL-T3", "decision": None}]},
    }


class _Model:
    def __init__(self, value):
        self.value = value

    def model_dump(self, **_):
        return deepcopy(self.value)


def _mock_database(monkeypatch, review, *, pointer="rev-current", generation=3, forbidden=False):
    class Connection:
        @asynccontextmanager
        async def transaction(self, **options):
            assert options == {"isolation": "repeatable_read", "readonly": True}
            yield

        async def fetchrow(self, _query, job_id):
            assert job_id == "job-1"
            return {"current_fact_revision_id": pointer, "analysis_revision": generation}

    async def with_connection(callback):
        return await callback(Connection())

    async def load_review(_conn, _job):
        return SimpleNamespace(review_context=SimpleNamespace(available=True), review=_Model(review))

    async def load_slot(_self, slot_id):
        assert slot_id == "slot-1"
        return {"id": slot_id}

    def require_access(_scope, slot, slot_id):
        assert slot["id"] == slot_id == "slot-1"
        if forbidden:
            raise HTTPException(403, "slot access denied")

    monkeypatch.setattr(material_access, "with_connection", with_connection)
    monkeypatch.setattr(material_access, "require_slot_access", require_access)
    monkeypatch.setattr(MaterialDetailQueryService, "load_slot_row", load_slot)
    monkeypatch.setattr(review_lifecycle_service, "load_review_by_job", load_review)


@pytest.mark.parametrize("completed", [False, True])
def test_current_or_completed_session_is_exported_without_changing_engine_coverage(monkeypatch, completed):
    review = _review()
    if completed:
        session = review["current_session"]
        session["status"] = "completed"
        review.update(current_session=None, history=[session])
    _mock_database(monkeypatch, review)
    payload = {"analysis_revision": "rev-current", "structured_ingest": {"document_version_id": 7},
               "result": {"meta": {"obligation_coverage": {"blocking_total": 2}}}}
    original = deepcopy(payload)
    block = asyncio.run(reports._load_export_obligation_review("job-1", payload, {"is_admin": True}))
    assert block["review_session_id"] == "session-1"
    assert block["analysis_basis_token"] == "job-1:3"
    assert block["pending_total"] == 1
    assert block["items"][0]["note"] == review["obligation_review"]["items"][0]["note"]
    assert len(block["items"]) == 1
    assert payload == original


@pytest.mark.parametrize("mismatch", ["historical", "job", "generation", "document", "session"])
def test_current_decisions_cannot_leak_into_another_report_identity(monkeypatch, mismatch):
    review = _review()
    pointer = "rev-new" if mismatch == "historical" else "rev-current"
    if mismatch == "job":
        review["current_analysis"]["job_uuid"] = "other-job"
    if mismatch == "generation":
        review["current_analysis"]["analysis_revision"] = 4
    if mismatch == "document":
        review["current_analysis"]["document_version_id"] = 8
    if mismatch == "session":
        review["current_session"]["analysis_basis_token"] = "job-1:2"
    _mock_database(monkeypatch, review, pointer=pointer)
    block = asyncio.run(reports._load_export_obligation_review(
        "job-1", {"analysis_revision": "rev-current", "structured_ingest": {"document_version_id": 7}}, {"is_admin": True}))
    assert block["available"] is False
    assert "items" not in block


def test_export_checks_slot_permission_in_addition_to_job_permission(monkeypatch):
    _mock_database(monkeypatch, _review(), forbidden=True)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(reports._load_export_obligation_review(
            "job-1", {"analysis_revision": "rev-current", "structured_ingest": {"document_version_id": 7}}, {"is_admin": False}))
    assert exc.value.status_code == 403


def test_four_format_downloads_include_manual_obligation_records_and_refresh_pdf_cache(tmp_path, monkeypatch):
    fitz = pytest.importorskip("fitz")
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    job_dir = tmp_path / "job-1"
    job_dir.mkdir()
    with fitz.open() as doc:
        doc.new_page().insert_text((72, 72), "Source document")
        doc.save(job_dir / "source.pdf")
    payload = {"job_id": "job-1", "analysis_revision": "rev-current", "status": "review_required",
               "saved_path": "job-1/source.pdf", "structured_ingest": {"document_version_id": 7},
               "result": {"rule_findings": [], "meta": {"obligation_coverage": {
                   "blocking_total": 2, "unresolved_total": 2, "instances": []}}}}
    (job_dir / "status.json").write_text(json.dumps(payload))
    block = {"available": True, "review_session_id": "session-1", "analysis_basis_token": "job-1:3",
             "document_version_id": 7, "session_status": "in_progress", "items": _review()["obligation_review"]["items"][:1]}

    async def published(_job, _revision=None):
        return deepcopy(payload)

    async def decisions(_job, _payload, _user):
        return deepcopy(block)

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused-for-mocked-export")
    monkeypatch.setattr(analysis_fact_snapshot, "read_database_result", published)
    monkeypatch.setattr(reports, "_load_export_obligation_review", decisions)
    client = TestClient(app)
    note = block["items"][0]["note"]
    for fmt in ("json", "csv", "docx", "pdf"):
        response = client.get(f"/api/reports/download?job_id=job-1&format={fmt}")
        assert response.status_code == 200, response.text
        if fmt == "json":
            data = response.json()
            assert data["obligation_review"]["items"][0]["decision"] == "verified_issue"
            assert data["review_report"]["obligation_coverage"]["blocking_total"] == 2
            text = json.dumps(data, ensure_ascii=False)
        elif fmt == "csv":
            rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
            row = next(x for x in rows if x["record_type"] == "obligation_decision")
            assert row["decision_actor"] == "reviewer"
            assert row["obligation_ids"] == "OBL-T2"
            assert json.loads(row["detail_lines"])["review_session_id"] == "session-1"
            text = row["human_decision_note"]
        elif fmt == "docx":
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                text = "".join(ET.fromstring(archive.read("word/document.xml")).itertext())
        else:
            with fitz.open(stream=response.content, filetype="pdf") as doc:
                text = "\n".join(page.get_text() for page in doc)
        assert note in text
    old_files = set(job_dir.glob("annotated-*.pdf"))
    block["items"][0].update(note="第8页重新核对并补充说明", decision_revision=3)
    response = client.get("/api/reports/download?job_id=job-1&format=pdf")
    assert response.status_code == 200
    assert set(job_dir.glob("annotated-*.pdf")) > old_files
    with fitz.open(stream=response.content, filetype="pdf") as doc:
        assert block["items"][0]["note"] in "\n".join(page.get_text() for page in doc)
