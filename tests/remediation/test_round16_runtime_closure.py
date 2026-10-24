"""Runtime closure: terminal cancellation reaps children; ingest errors cannot pass."""

from __future__ import annotations

import asyncio
import hashlib
import json
import multiprocessing
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts.run_isolated_service_validation import structured_ingest_validation
from src.schemas.parse_artifact import ParseArtifact


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["pdf", "rules"])
@pytest.mark.parametrize("cause", ["timeout", "cancel"])
async def test_pipeline_terminal_state_has_no_live_stage_process(kind, cause, tmp_path, monkeypatch):
    import fitz
    from api import main
    from src.services.pdf_parse_process import parse_pdf_in_process
    from src.services.rule_process import run_rules_in_process

    source = tmp_path / "source.pdf"
    with fitz.open() as document:
        document.new_page()
        document.save(source)
    original_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    (tmp_path / "status.json").write_text(json.dumps({"job_id": tmp_path.name, "status": "queued", "ts": time.time()}))
    monkeypatch.setenv("PIPELINE_TIMEOUT_SEC", "1" if cause == "timeout" else "60")
    monkeypatch.setenv("PDF_PARSE_TEST_DELAY_SECONDS", "30")
    monkeypatch.setenv("RULES_PROCESS_TEST_DELAY_SECONDS", "30")
    monkeypatch.setenv("RULES_PROCESS_START_METHOD", "spawn")
    monkeypatch.setattr(main, "persist_analysis_job_snapshot", AsyncMock())

    async def stage(_directory):
        if kind == "pdf":
            await parse_pdf_in_process(source, {"timeout_seconds": 40, "memory_mb": 0})
        else:
            await run_rules_in_process(None, timeout_seconds=40)

    monkeypatch.setattr(main, "_run_pipeline_inner", stage)
    started = time.monotonic()
    task = asyncio.create_task(main._run_pipeline(tmp_path))
    expected_name = "govbudget-pdf-parser" if kind == "pdf" else "govbudget-rule-evaluator"
    while not any(p.name == expected_name for p in multiprocessing.active_children()):
        assert not task.done(), "the real isolated stage must be entered"
        await asyncio.sleep(.01)
    if cause == "cancel":
        (tmp_path / ".cancel-request.json").write_text(json.dumps({"ts": time.time()}))
    await asyncio.wait_for(task, 8)
    status = json.loads((tmp_path / "status.json").read_text())
    assert status["error"] == ("pipeline_timeout_1s" if cause == "timeout" else "pipeline_cancelled")
    assert time.monotonic() - started < 8
    assert not any(p.name == expected_name and p.is_alive() for p in multiprocessing.active_children())
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_sha


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["parse", "facts", "materialization", "wrong_count"])
async def test_authoritative_ingest_write_failure_is_error_with_version_context(failure_stage, tmp_path, monkeypatch):
    from src.services import structured_ingest_runner as runner

    artifact = ParseArtifact(source_file_hash="a" * 64, page_count=1)
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.4\n%%EOF\n")
    artifact.source_file_hash = hashlib.sha256(source.read_bytes()).hexdigest()

    async def parse_write(*_args, **_kwargs):
        if failure_stage == "parse":
            raise OSError("injected artifact write failure")
        return 42

    async def facts_write(*_args, **_kwargs):
        if failure_stage == "facts":
            raise OSError("injected facts write failure")
        return 0 if failure_stage == "wrong_count" else 1

    def materialize(*_args, **_kwargs):
        if failure_stage == "materialization":
            raise ValueError("injected materialization failure")
        from src.engine.fact_context import FactContext
        from src.schemas.parse_artifact import FiscalFact
        return FactContext([FiscalFact('fact','total_actual','1','万元',__import__('decimal').Decimal('1'))],
                           file_hash=artifact.source_file_hash,parse_run_uid=artifact.parse_run_uid)

    class LegacyParser:
        def __init__(self, _connection):
            pass
        async def persist_artifact_to_legacy_tables(self, *_args):
            return {"success": True, "tables_count": 9}

    class Recognizer:
        def __init__(self, _connection):
            pass
        async def recognize_tables(self, *_args):
            return []
        async def save_table_instances(self, *_args):
            return None

    class LegacyMaterializer:
        def __init__(self, _connection):
            pass
        async def materialize(self, *_args):
            return {"facts_count": 2}

    for name, replacement in {
        "ensure_structured_ingest_ready": AsyncMock(return_value=True),
        "_ensure_document_version": AsyncMock(return_value={"document_id": 7, "document_version_id": 9}),
        "_allocate_material_slot": AsyncMock(return_value={"status": "allocated"}),
        "find_cached_parse_run": AsyncMock(return_value=None), "persist_parse_artifact": parse_write,
        "load_parse_artifact": AsyncMock(return_value=ParseArtifact.from_dict(artifact.to_dict())),
        "persist_facts_and_evidence": facts_write,
        "verify_persisted_facts": AsyncMock(return_value={'verified':True}),
        "PDFParser": LegacyParser, "TableRecognizer": Recognizer, "FiscalFactMaterializer": LegacyMaterializer,
    }.items():
        monkeypatch.setattr(runner, name, replacement)
    monkeypatch.setattr('src.engine.fact_context.build_fact_context_from_artifact',materialize)
    monkeypatch.setattr(runner.DatabaseConnection, "acquire", AsyncMock(return_value=object()))
    monkeypatch.setattr(runner.DatabaseConnection, "release", AsyncMock())
    result = await runner.run_structured_ingest("job", source,
        {"organization_name": "测试局", "report_year": 2024, "doc_type": "dept_final", "checksum": artifact.source_file_hash}, parse_artifact=artifact)
    assert result["status"] == "error"
    assert result["unified_storage"]["status"] == "error"
    assert result["document_version_id"] == 9 and result["material_slot"]["status"] == "allocated"
    assert result["review_items"][0]["type"] == "ingest_error"
    payload = {"job_id": "job", "analysis_revision": "rev", "result": {"meta": {"structured_ingest": result}}}
    database = {"job": {"file_hash": artifact.source_file_hash, 'current_fact_revision_id':'rev'}, 'snapshot_mirror_pending':False, "result_revision": "rev", "result_ingest_status": "done",
                "run_facts_count": 999, "run_evidence_count": 999,
                "effective_compatibility_projection_verified": True,
                "parse_run": {"id": 42, "document_version_id": 9, "status": "completed", "source_file_hash": artifact.source_file_hash, "version_file_hash": artifact.source_file_hash}}
    assert structured_ingest_validation(payload, database)["passed"] is False

    class Sync:
        def __init__(self, _connection):
            pass
        async def sync(self, **_kwargs):
            return {"status": "done", "report_type": "FINAL"}

    # Repair the failing dependency and retry the same source/version. A genuine
    # completed write gets a positive receipt, rather than staying permanently
    # blocked or merely borrowing the former row count.
    monkeypatch.setattr(runner, "persist_parse_artifact", AsyncMock(return_value=42))
    monkeypatch.setattr(runner, "persist_facts_and_evidence", AsyncMock(return_value=1))
    from src.engine.fact_context import FactContext
    from src.schemas.parse_artifact import FiscalFact
    monkeypatch.setattr('src.engine.fact_context.build_fact_context_from_artifact', lambda *_args, **_kwargs:
        FactContext([FiscalFact('fact','total_actual','1','万元',__import__('decimal').Decimal('1'))],
                    file_hash=artifact.source_file_hash,parse_run_uid=artifact.parse_run_uid))
    monkeypatch.setattr(runner, "PSSharedSchemaSync", Sync)
    monkeypatch.setattr(runner, "_detect_document_profile", lambda *_args: None)
    monkeypatch.setattr(runner, "_backfill_local_organization_catalog", lambda **_kwargs: {})
    monkeypatch.setattr(runner, "_build_review_items", lambda **_kwargs: [])
    retry = await runner.run_structured_ingest("job", source,
        {"organization_name": "测试局", "report_year": 2024, "doc_type": "dept_final", "checksum": artifact.source_file_hash}, parse_artifact=artifact)
    assert retry["status"] == "done" and retry["document_version_id"] == result["document_version_id"]
    assert retry["unified_storage"]["status"] == "completed"
    payload["result"]["meta"]["structured_ingest"] = retry
    assert structured_ingest_validation(payload, database)["passed"] is True


def test_legitimate_empty_fact_write_is_completed_and_missing_write_receipt_is_rejected():
    ingest = {"status": "done", "job_id": "job", "parse_run_id": 4, "document_version_id": 5, "unified_facts_count": 0,
              "unified_storage": {"status": "completed", "artifact_status": "completed", "facts_status": "completed",
                                  "evidence_status": "completed", "expected_facts_count": 0, "parse_run_id": 4, "document_version_id": 5}}
    payload = {"job_id": "job", "analysis_revision": "rev", "result": {"meta": {"structured_ingest": ingest}}}
    database = {"job": {"file_hash": "a" * 64, 'current_fact_revision_id':'rev'}, 'snapshot_mirror_pending':False, "result_revision": "rev", "result_ingest_status": "done",
                "run_facts_count": 0, "run_evidence_count": 0, "effective_compatibility_projection_verified": True,
                "parse_run": {"id": 4, "document_version_id": 5,
                    "status": "completed", "source_file_hash": "a" * 64, "version_file_hash": "a" * 64}}
    assert structured_ingest_validation(payload, database)["passed"]
    ingest.pop("unified_storage")
    database.update(run_facts_count=999, run_evidence_count=999)
    assert not structured_ingest_validation(payload, database)["passed"]


def test_ingest_failure_is_visible_to_quality_gate_and_parser_quality():
    from api import main
    ingest = {"status": "error", "ingest_stage": "unified_facts_evidence_write", "review_items": [{"type": "ingest_error"}]}
    assessment = {"page_coverage": 1.0, "scanned_page_count": 0}
    gate = main._evaluate_quality_gate(assessment, "final", 2024, False, False, 1, structured_ingest=ingest)
    assert gate["status"] == "review_required"
    assert any(reason["code"] == "structured_ingest_failed" for reason in gate["review_reasons"])
    assert main._assess_parser_quality(assessment, ingest)["quality"] == "poor"
