"""Read-only isolated probes; no service or production database access."""
from __future__ import annotations

import asyncio
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import sys
import time
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
OUT = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
os.environ.update(PYTHON_DOTENV_DISABLED="1", TESTING="true", GOVBUDGET_AUTH_ENABLED="false",
                  DATABASE_URL="", USER_DATA_DIR=str(OUT / "users"), ORG_DATA_DIR=str(OUT / "orgs"),
                  UPLOAD_DIR=str(OUT / "uploads"), AUDIT_LOG_PATH=str(OUT / "audit.jsonl"),
                  AI_ENABLED="false", AI_ASSIST_ENABLED="false", LOG_JSON="true")


def fingerprint_check():
    manifest = json.loads((ROOT / "outputs/remediation_20261007_round15/independent_intake_final/code_manifest.json").read_text())
    return {"registered_count": len(manifest), "mismatches": [path for path, digest in manifest.items()
        if not (ROOT / path).is_file() or hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != digest]}


async def subprocess_cancellation_probe(kind, cause):
    import fitz
    from api import main
    from src.services.pdf_parse_process import parse_pdf_in_process
    from src.services.rule_process import run_rules_in_process

    directory = OUT / f"isolated-{kind}-{cause}"
    directory.mkdir(exist_ok=True)
    pdf = directory / "controlled.pdf"
    with fitz.open() as document:
        document.new_page()
        document.save(pdf)
    (directory / "status.json").write_text(json.dumps({"job_id": directory.name, "status": "queued", "ts": time.time()}))
    started = asyncio.Event()

    async def real_stage(_directory):
        started.set()
        if kind == "pdf":
            await parse_pdf_in_process(pdf, {"timeout_seconds": 5, "start_method": "fork", "memory_mb": 0})
        else:
            await run_rules_in_process(None, timeout_seconds=5)

    env = {"PIPELINE_TIMEOUT_SEC": "1" if cause == "timeout" else "60", "OCR_ENABLED": "false",
           "DATABASE_URL": "", "PDF_PARSE_TEST_DELAY_SECONDS": "3", "RULES_PROCESS_TEST_DELAY_SECONDS": "3",
           "RULES_PROCESS_START_METHOD": "fork"}
    with patch.dict(os.environ, env), patch.object(main, "_run_pipeline_inner", real_stage), patch.object(main, "persist_analysis_job_snapshot", AsyncMock()):
        start = time.monotonic()
        task = asyncio.create_task(main._run_pipeline(directory))
        await started.wait()
        while not multiprocessing.active_children():
            await asyncio.sleep(.01)
        if cause == "cancel":
            (directory / ".cancel-request.json").write_text(json.dumps({"job_id": directory.name, "ts": time.time()}))
        await task
        elapsed = time.monotonic() - start
        children_after_terminal = [{"pid": p.pid, "name": p.name, "alive": p.is_alive()} for p in multiprocessing.active_children()]
        status = json.loads((directory / "status.json").read_text())
        # Wait for the deliberately short stage to end; never leave probe writers.
        while multiprocessing.active_children() and time.monotonic() - start < 7:
            await asyncio.sleep(.05)
        remaining = multiprocessing.active_children()
        for child in remaining:
            child.terminate()
            child.join(timeout=2)
        return {"kind": kind, "cause": cause, "terminal_status": status.get("status"),
                "terminal_error": status.get("error"), "terminal_seconds": round(elapsed, 3),
                "declared_cancellation_scope": status.get("pipeline_budget", {}).get("cancellation_scope"),
                "children_alive_after_terminal": children_after_terminal,
                "children_stopped_by_probe_end": not multiprocessing.active_children(),
                "source_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest()}


async def swallowed_ingest_probe(failure_stage):
    from src.services import structured_ingest_runner as runner
    from src.schemas.parse_artifact import ParseArtifact
    from scripts.run_isolated_service_validation import structured_ingest_validation

    artifact = ParseArtifact(source_file_hash="a" * 64, page_count=1, parse_run_uid="same-run")
    pdf = OUT / "controlled-ingest.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    calls = []

    async def persist_parse(*_args, **_kwargs):
        calls.append("persist_parse_artifact")
        if failure_stage == "parse":
            raise OSError("injected unified parse write failure")
        return 99

    async def persist_facts(*_args, **_kwargs):
        calls.append("persist_facts_and_evidence")
        if failure_stage == "facts":
            raise OSError("injected unified facts write failure")
        return 1

    class Parser:
        def __init__(self, _connection):
            pass
        async def persist_artifact_to_legacy_tables(self, *_args):
            return {"success": True, "tables_count": 9, "unknown_tables": []}

    class Recognizer:
        def __init__(self, _connection):
            pass
        async def recognize_tables(self, *_args):
            return []
        async def save_table_instances(self, *_args):
            return None

    class Materializer:
        def __init__(self, _connection):
            pass
        async def materialize(self, *_args):
            return {"facts_count": 2, "low_confidence_tables": []}

    class Sync:
        def __init__(self, _connection):
            pass
        async def sync(self, **_kwargs):
            return {"status": "done", "report_type": "FINAL"}

    with ExitStack() as stack:
        overrides = {"ensure_structured_ingest_ready": AsyncMock(return_value=True),
                     "_ensure_document_version": AsyncMock(return_value={"document_id": 8, "document_version_id": 9}),
                     "_allocate_material_slot": AsyncMock(return_value={"status": "allocated"}),
                     "find_cached_parse_run": AsyncMock(return_value=None),
                     "persist_parse_artifact": persist_parse, "persist_facts_and_evidence": persist_facts,
                     "PDFParser": Parser, "TableRecognizer": Recognizer, "FiscalFactMaterializer": Materializer,
                     "PSSharedSchemaSync": Sync, "_detect_document_profile": lambda *_: None,
                     "_backfill_local_organization_catalog": lambda **_: {},
                     "_build_review_items": lambda **_: [],
                     "_materialize_fiscal_facts_from_artifact": lambda *_args, **_kwargs: ["intended-new-fact"]}
        for name, replacement in overrides.items():
            stack.enter_context(patch.object(runner, name, replacement))
        stack.enter_context(patch.object(runner.DatabaseConnection, "acquire", AsyncMock(return_value=object())))
        stack.enter_context(patch.object(runner.DatabaseConnection, "release", AsyncMock()))
        result = await runner.run_structured_ingest("probe", pdf,
            {"organization_name": "隔离验证局", "report_year": 2024, "doc_type": "dept_final", "checksum": "a" * 64}, parse_artifact=artifact)
    payload = {"job_id": "probe", "analysis_revision": "rev-current", "result": {"meta": {"structured_ingest": result}}}
    database = {"job": {"file_hash": "a" * 64, "analysis_revision": 1}, "result_revision": "rev-current",
                "result_ingest_status": result["status"], "run_facts_count": 1, "run_evidence_count": 1,
                "parse_run": {"id": 99, "document_version_id": 9, "status": "completed",
                              "source_file_hash": "a" * 64, "version_file_hash": "a" * 64}}
    return {"failure_stage": failure_stage, "calls": calls, "structured_ingest": result,
            "validation_with_preexisting_same_run_rows": structured_ingest_validation(payload, database),
            "scope": "deterministic injected write failure; fake DB connector; production function and acceptance predicate unchanged"}


async def main():
    receipt = {"source_before": fingerprint_check(), "probes": [], "external_services_started": False,
               "production_database_accessed": False, "plan_refs": ["PLAN V2.0 WP10/Gate A-F", "PLAN V1.0 Gate F任务失败可重试/原PDF SHA不变"]}
    for kind in ("pdf", "rules"):
        for cause in ("timeout", "cancel"):
            receipt["probes"].append(await subprocess_cancellation_probe(kind, cause))
    receipt["ingest_write_failure_probes"] = [await swallowed_ingest_probe(stage) for stage in ("parse", "facts")]
    receipt["source_after"] = fingerprint_check()
    (OUT / "runtime_audit.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(receipt, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
