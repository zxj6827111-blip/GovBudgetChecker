"""R14-04/05: page budgets, real cache codecs, cancellation and honest receipts."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from src.schemas.parse_artifact import ParseArtifact
from src.services.parse_artifact_store import _decode_jsonb, load_parse_artifact
from scripts import run_isolated_service_validation as validation


@pytest.mark.parametrize("value", [None, "null", {}, "{}", {"request": {"page": 1}}, '{"request":{"page":1}}'])
def test_jsonb_object_accepts_default_asyncpg_and_explicit_codec(value):
    decoded = _decode_jsonb(value, dict, field="ocr_provenance")
    assert decoded == ({"request": {"page": 1}} if value and value != "{}" and value != "null" else {})


@pytest.mark.parametrize("value", [[], "[]", 0, False, '"text"', "malformed", "0"])
def test_jsonb_object_rejects_corruption(value):
    with pytest.raises(ValueError, match="ocr_provenance"):
        _decode_jsonb(value, dict, field="ocr_provenance")


@pytest.mark.asyncio
@pytest.mark.parametrize("codec", ["default", "json"])
async def test_actual_cached_cell_load_keeps_nested_ocr_provenance(codec):
    def encoded(value):
        return json.dumps(value) if codec == "default" else value

    provenance = {"page_request": {"physical_page": 1, "source_sha256": "a" * 64}, "region": [1, 2]}
    run = dict(source_file_hash="a" * 64, parser_name="pdfplumber", parser_fingerprint="cells",
               config_fingerprint="cfg", ocr_model_fingerprint="b" * 64, parse_run_uid="run", page_count=1,
               page_states=encoded([]), text_blocks=encoded([]), ocr_table_candidates=encoded([]),
               coordinate_space="pdf_page_points", coordinate_transforms=encoded({"render": provenance}))
    instance = dict(instance_uid="table", table_code="FIN_02_income", title="收入决算表",
                    page_start=1, page_end=1, org_name="测试局", fiscal_year=2024, fund_scope="全口径",
                    row_count=1, confidence=.99, column_mappings=encoded([]), identity_conflict=False,
                    conflict_reasons=encoded([]))
    cell = dict(instance_uid="table", row_idx=1, col_idx=1, raw_text="746.62", normalized_text="746.62",
                numeric_value=746.62, page_number=1, bbox=encoded([10, 10, 20, 20]), is_header=False, confidence=.99,
                unit_hint="万元", extraction_method="ocr", row_span=1, col_span=1,
                origin_fragment_uid=None, origin_row_idx=None, normalized_value_wan="746.62",
                presence="value", ocr_provenance=encoded(provenance))

    class Connection:
        async def fetchrow(self, *_args):
            return run

        async def fetch(self, query, *_args):
            if "parse_table_instances" in query:
                return [instance]
            if "parse_table_cells" in query:
                return [cell]
            return []

    restored = await load_parse_artifact(Connection(), 1)
    assert restored.cells()[0][1].ocr_provenance == provenance
    assert restored.cells()[0][1].raw_text == "746.62"
    assert restored.cells()[0][1].bbox == (10, 10, 20, 20)
    assert restored.coordinate_transforms["render"] == provenance


def test_page_aware_worker_budget_preserves_explicit_and_hard_limits(tmp_path, monkeypatch):
    from api import main
    monkeypatch.setattr(main.runtime, "find_first_pdf", lambda _: tmp_path / "source.pdf")
    monkeypatch.setattr(main.runtime, "get_pdf_page_count", lambda _: 21)
    monkeypatch.setenv("OCR_ENABLED", "true")
    monkeypatch.delenv("PIPELINE_TIMEOUT_SEC", raising=False)
    budget = main._resolve_pipeline_budget(tmp_path)
    assert budget["total_seconds"] == 7200
    assert budget['ocr_page_seconds']==600
    assert budget["physical_page_count"] == 21
    monkeypatch.setenv("PIPELINE_TIMEOUT_SEC", "1")
    assert main._resolve_pipeline_budget(tmp_path)["total_seconds"] == 1
    monkeypatch.setenv("PIPELINE_TIMEOUT_SEC", "999999")
    assert main._resolve_pipeline_budget(tmp_path)["total_seconds"] == 7200


@pytest.mark.asyncio
async def test_cancellation_stops_inflight_pipeline_and_preserves_resume_checkpoint(tmp_path, monkeypatch):
    from api import main
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def blocked(_directory):
        started.set()
        try:
            await asyncio.sleep(9999)
        finally:
            cancelled.set()

    monkeypatch.setattr(main, "_run_pipeline_inner", blocked)
    monkeypatch.setattr(main, "persist_analysis_job_snapshot", AsyncMock())
    (tmp_path / "status.json").write_text(json.dumps({"status": "queued", "ts": 1}))
    (tmp_path / "ocr_checkpoint.json").write_text('{"pages":{"1":"completed"}}')
    pipeline = asyncio.create_task(main._run_pipeline(tmp_path))
    await started.wait()
    (tmp_path / ".cancel-request.json").write_text(json.dumps({"ts": 2}))
    await asyncio.wait_for(pipeline, 3)
    assert cancelled.is_set()
    status = json.loads((tmp_path / "status.json").read_text())
    assert status["status"] == "error" and status["error"] == "pipeline_cancelled"
    assert status["pipeline_budget"]["outcome"] == "cancelled"
    assert (tmp_path / "ocr_checkpoint.json").exists()


@pytest.mark.asyncio
async def test_authorized_cancel_endpoint_creates_request_marker(tmp_path, monkeypatch):
    from api.routes import jobs
    directory = tmp_path / "job"
    directory.mkdir()
    monkeypatch.setattr(jobs.runtime, "UPLOAD_ROOT", tmp_path)
    monkeypatch.setattr(jobs, "require_job_access", lambda *_: (None, None, {"username": "tester"}, {"status": "processing"}))
    monkeypatch.setattr(jobs, "append_audit_event", lambda **_: None)
    response = await jobs.cancel_job("job", object())
    assert response["status"] == "cancel_requested"
    assert json.loads((directory / ".cancel-request.json").read_text())["job_id"] == "job"


@pytest.mark.asyncio
async def test_incremental_checkpoint_reuses_only_same_source_page_model_and_render(tmp_path, monkeypatch):
    from src.services import ocr_pipeline
    from src.services.ocr_client import OcrPageResult
    image_buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(image_buffer, format="PNG")
    png = image_buffer.getvalue()
    render_sha = hashlib.sha256(png).hexdigest()
    calls = []

    def render(_path, pages):
        page = pages[0]
        return [png], {page: {"physical_page": page, "source_sha256": "a" * 64, "render_sha256": render_sha,
                            "pixel_to_pdf_matrix": [1, 0, 0, 1, 0, 0]}}

    async def recognize(_images, pages):
        calls.extend(pages)
        raw = {"page": pages[0]}
        raw_sha = hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return [OcrPageResult(page_number=pages[0], provenance={"input_sha256": render_sha,
                    "raw_prediction": raw, "raw_prediction_sha256": raw_sha,
                    "model_fingerprint": "b" * 64, "pipeline_fingerprint": "c" * 64})]

    monkeypatch.setattr(ocr_pipeline, "_render_pages_png", render)
    monkeypatch.setattr(ocr_pipeline, "ocr_pages", recognize)
    checkpoint = tmp_path / "checkpoint.json"
    artifact = ParseArtifact(source_file_hash="a" * 64, config_fingerprint="cfg", ocr_model_fingerprint="b" * 64)
    health = {"pipeline_fingerprint": "c" * 64}
    options = dict(progress_callback=None, checkpoint_path=checkpoint)
    first = await ocr_pipeline._recognize_incrementally("source.pdf", [1, 2], artifact, health, **options)
    assert calls == [1, 2] and first[2]["reused_pages"] == []
    assert first[1][1]["page_request"]["physical_page"] == 1
    second = await ocr_pipeline._recognize_incrementally("source.pdf", [1, 2], artifact, health, **options)
    assert calls == [1, 2] and second[2]["reused_pages"] == [1, 2]
    saved = json.loads(checkpoint.read_text())
    saved["pages"]["1"]["result"]["page_number"] = 2
    checkpoint.write_text(json.dumps(saved))
    third = await ocr_pipeline._recognize_incrementally("source.pdf", [1, 2], artifact, health, **options)
    assert calls == [1, 2, 1] and third[2]["reused_pages"] == [2]
    saved = json.loads(checkpoint.read_text())
    saved["pages"]["1"]["transform"]["pixel_to_pdf_matrix"] = [99, 0, 0, 99, 0, 0]
    checkpoint.write_text(json.dumps(saved))
    fourth = await ocr_pipeline._recognize_incrementally("source.pdf", [1, 2], artifact, health, **options)
    assert calls[-1] == 1 and fourth[2]["reused_pages"] == [2]
    assert fourth[1][1]["pixel_to_pdf_matrix"] == [1, 0, 0, 1, 0, 0]
    checkpoint.write_text('"invalid cache scalar"')
    await ocr_pipeline._recognize_incrementally("source.pdf", [1, 2], artifact, health, **options)
    assert calls[-2:] == [1, 2]


def test_ingest_receipt_rejects_error_and_stale_rows_even_with_many_prior_facts():
    ingest = {"status": "done", "job_id": "job", "parse_run_id": 4, "document_version_id": 5, "unified_facts_count": 1}
    ingest["unified_storage"] = {"status": "completed", "artifact_status": "completed", "facts_status": "completed",
                                "evidence_status": "completed", "parse_run_id": 4, "document_version_id": 5, "expected_facts_count": 1}
    payload = {"job_id": "job", "analysis_revision": "rev-current", "result": {"meta": {"structured_ingest": ingest}}}
    database = {"job": {"file_hash": "a" * 64, "analysis_revision": 2, 'current_fact_revision_id':'rev-current'}, 'snapshot_mirror_pending':False, "result_revision": "rev-current",
                "result_ingest_status": "done", "run_facts_count": 1, "run_evidence_count": 1,
                "effective_compatibility_projection_verified": True,
                "parse_run": {"id": 4, "document_version_id": 5, "status": "completed",
                              "source_file_hash": "a" * 64, "version_file_hash": "a" * 64}}
    assert validation.structured_ingest_validation(payload, database)["passed"]
    database['effective_compatibility_projection_verified'] = False
    assert not validation.structured_ingest_validation(payload, database)["passed"]
    database['effective_compatibility_projection_verified'] = True
    ingest["status"] = "error"
    assert not validation.structured_ingest_validation(payload, database)["passed"]
    ingest["status"] = "done"
    database["result_revision"] = "rev-old"
    assert not validation.structured_ingest_validation(payload, database)["passed"]


def test_cleanup_oserror_still_writes_failed_final_receipt(tmp_path, monkeypatch):
    def fail(*_args, **_kwargs):
        raise OSError("injected cleanup failure")
    monkeypatch.setattr(validation.sys, "argv", ["validation", "--output", str(tmp_path)])
    monkeypatch.setattr(validation, "docker", lambda *_: (_ for _ in ()).throw(RuntimeError("setup failure")))
    monkeypatch.setattr(validation.subprocess, "run", fail)
    assert validation.main() == 2
    receipt = json.loads((tmp_path / "service_receipt.json").read_text())
    assert receipt["status"] == "failed" and receipt["postgres_removed"] is False
    assert any("injected cleanup failure" in error for error in receipt["errors"])
