"""Actual pipeline/corrections/decisions/downloads: five versions and tampering.

Synthetic PDF, isolated filesystem and no database; this is a production-function
regression, not a substitute for independent blind truth or deployed OCR.
"""

import asyncio
import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET

from fastapi.testclient import TestClient

from api import main, runtime
from scripts.run_zc07_acceptance import _evaluate_export_agreement


def test_historical_docx_bytes_do_not_depend_on_download_time(monkeypatch):
    from api.routes import reports
    from api.routes.reports import _create_docx_report

    status = {
        "status": "done",
        "filename": "历史报告.pdf",
        "analysis_revision": "rev-history",
        "result": {"rule_findings": []},
    }
    monkeypatch.setattr(
        reports,
        "datetime",
        SimpleNamespace(
            now=lambda *_args: datetime(2026, 10, 5, tzinfo=timezone.utc),
        ),
        raising=False,
    )
    monkeypatch.setattr(zipfile.time, "localtime", lambda *args: (2026, 10, 5, 1, 2, 4, 0, 0, 0))
    first = _create_docx_report("historical", status, [])
    monkeypatch.setattr(
        reports,
        "datetime",
        SimpleNamespace(
            now=lambda *_args: datetime(2026, 10, 6, tzinfo=timezone.utc),
        ),
        raising=False,
    )
    monkeypatch.setattr(zipfile.time, "localtime", lambda *args: (2026, 10, 6, 5, 6, 8, 0, 0, 0))
    second = _create_docx_report("historical", status, [])
    assert first == second
    with zipfile.ZipFile(io.BytesIO(second)) as archive:
        assert all(entry.date_time == (1980, 1, 1, 0, 0, 0) for entry in archive.infolist())


def create_pdf(path):
    import fitz

    with fitz.open() as document:
        page = document.new_page(width=650, height=700)
        for point, text in [
            ((40, 40), "2026年测试局部门预算公开"),
            ((40, 70), "部门预算收支总表"),
            ((40, 95), "单位：万元"),
            ((40, 260), "一、收支预算总体情况说明"),
            ((40, 280), "2026年收入预算90万元，支出预算100万元。"),
        ]:
            page.insert_text(point, text, fontname="china-s", fontsize=11)
        xs, ys = [40, 200, 320, 480, 610], [115, 155, 195, 235]
        for x in xs:
            page.draw_line((x, 115), (x, 235))
        for y in ys:
            page.draw_line((40, y), (610, y))
        rows = [
            ["收入项目", "本年收入", "支出项目", "本年支出"],
            ["一般公共预算", "100.00", "基本支出", "100.00"],
            ["收入总计", "100.00", "支出总计", "100.00"],
        ]
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                page.insert_text((xs[c] + 6, ys[r] + 25), value, fontname="china-s", fontsize=10)
        document.save(path)


def test_pdf_download_preserves_mathematical_minus_and_passes_gate(tmp_path, monkeypatch):
    import fitz
    from api.routes import reports

    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    job_id = "unicode-mathematical-minus"
    directory = tmp_path / job_id
    directory.mkdir()
    create_pdf(directory / "source.pdf")
    suggestion = "请按“基期=本期−增加额（或本期+减少额）”复算；用(本期−基期)/基期复算百分比。"
    status = {
        "job_id": job_id,
        "status": "done",
        "filename": "source.pdf",
        "analysis_revision": "rev-unicode-test",
        "result": {
            "rule_findings": [
                {
                    "id": "CMM-007-1",
                    "rule_id": "CMM-007",
                    "severity": "high",
                    "title": "同比复算",
                    "message": "基期为0时百分比无定义",
                    "suggestion": suggestion,
                    "location": {"page": 1},
                    "location_level": "unresolved",
                    "verification_status": "unverified",
                    "evidence": [],
                }
            ],
        },
    }
    (directory / "status.json").write_text(json.dumps(status), encoding="utf-8")
    # A renderer update creates a new derived file, preserving prior downloads.
    current_path = reports._annotated_output_path(job_id, status, [])
    with monkeypatch.context() as renderer_patch:
        renderer_patch.setattr(reports, "_PDF_EXPORT_RENDERER_VERSION", "previous-renderer")
        assert reports._annotated_output_path(job_id, status, []) != current_path
    client = TestClient(main.app)
    paths = []
    for suffix in ("json", "csv", "docx", "pdf"):
        response = client.get("/api/reports/download", params={"job_id": job_id, "format": suffix})
        assert response.status_code == 200, response.text
        path = tmp_path / f"report.{suffix}"
        path.write_bytes(response.content)
        paths.append(str(path))
    with fitz.open(tmp_path / "report.pdf") as document:
        text = "".join(page.get_text() for page in document)
    assert "".join(suggestion.split()) in "".join(text.split())
    gate = _evaluate_export_agreement(
        {"documents": [{"doc_id": job_id, "export_paths": paths}]}, tmp_path
    )
    assert gate["status"] == "computed" and gate["consistent_documents"] == 1, gate


def test_round6_five_download_sets_and_strict_tampering(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    monkeypatch.setattr(runtime, "require_user_store", lambda: object())
    for key, value in {
        "AI_ENABLED": "false",
        "OCR_ENABLED": "false",
        "UPLOAD_DIR": str(tmp_path),
        "PDF_PARSE_ISOLATION_ENABLED": "false",
        "RULE_EXECUTION_ISOLATION_ENABLED": "false",
        "AUDIT_LOG_PATH": str(tmp_path / "audit.jsonl"),
        "PIPELINE_TIMEOUT_SEC": "30",
        "JOB_QUEUE_ENABLED": "false",
        "JOB_QUEUE_INLINE_FALLBACK": "true",
    }.items():
        monkeypatch.setenv(key, value)
    job_id = "round6-production-chain"
    directory = tmp_path / job_id
    directory.mkdir()
    source = directory / "source.pdf"
    create_pdf(source)
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    initial = {
        "job_id": job_id,
        "filename": "source.pdf",
        "status": "queued",
        "mode": "legacy",
        "use_local_rules": True,
        "use_ai_assist": False,
        "fiscal_year": 2026,
        "report_year": 2026,
        "report_kind": "budget",
        "doc_type": "budget",
        "organization_name": "测试局",
        "created_by": "test-admin",
    }
    (directory / "status.json").write_text(json.dumps(initial), encoding="utf-8")
    asyncio.run(main._run_pipeline(directory))
    first = json.loads((directory / "status.json").read_text())
    def current_budget_finding(payload):
        # A legacy numeric mismatch without the full native narrative binding
        # remains reviewable, but cannot be asserted as a confirmed issue.
        report = payload["review_report"]
        findings = [*report["issues"], *(item["finding"] for item in report["manual_review"]
                     if item.get("record_type") == "finding_review_candidate")]
        return next(i for i in findings if i["rule_id"] == "BUD-107")

    issue = current_budget_finding(first)
    assert issue["evidence_verification"]["source_identity_complete"] is False
    assert not any(i["rule_id"] == "BUD-107" for i in first["review_report"]["issues"])
    revision = str(first["analysis_revision"])
    target = next(
        r["operand_fact_ids"][0]
        for r in first["result"]["meta"]["fact_receipts"]
        if r["sub_check_id"] == "T1:income_budget:text_vs_table"
    )
    monkeypatch.setattr(runtime, "_pipeline_runner", main._run_pipeline, raising=False)
    runtime.set_pipeline_runner(main._run_pipeline)
    client = TestClient(main.app)

    def decision(status, note):
        response = client.post(
            "/api/workflow",
            json={
                "action": "update_issue",
                "job_id": job_id,
                "issue_id": current_budget_finding(json.loads((directory / "status.json").read_text()))["id"],
                "status": status,
                "note": note,
            },
        )
        assert response.status_code == 200, response.text

    sets = []

    def capture(name, analysis_revision=None):
        destination = tmp_path / name
        destination.mkdir()
        paths = []
        for suffix in ("json", "csv", "docx", "pdf"):
            params = {"job_id": job_id, "format": suffix}
            if analysis_revision:
                params["analysis_revision"] = analysis_revision
            response = client.get("/api/reports/download", params=params)
            assert response.status_code == 200, response.text
            path = destination / f"report.{suffix}"
            path.write_bytes(response.content)
            paths.append(str(path))
        row = {"doc_id": name, "export_paths": paths}
        gate = _evaluate_export_agreement({"documents": [row]}, tmp_path)
        assert gate["status"] == "computed" and gate["consistent_documents"] == 1, gate
        sets.append(row)
        return json.loads((destination / "report.json").read_text())

    async def correct(value):
        from api.routes.corrections import CorrectionRequest, create_job_correction
        from src.services.correction_overlay_store import (
            current_analysis_basis_token,
            read_jsonl_records,
        )
        from starlette.requests import Request

        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": f"/api/jobs/{job_id}/corrections",
                "headers": [],
                "server": ("test", 80),
                "scheme": "http",
                "query_string": b"",
            }
        )
        basis = current_analysis_basis_token(job_id, directory)
        runtime.set_pipeline_runner(None)
        preparation = CorrectionRequest(
            analysis_basis_token=basis,
            reason="register original evidence",
            changes=[
                {
                    "target_type": "fact",
                    "target_id": target,
                    "field": "manual_location",
                    "value": {"physical_page": 1, "bbox": [205, 155, 315, 195], "quote": "100.00"},
                }
            ],
        )
        await create_job_correction(job_id, preparation, request)
        evidence = read_jsonl_records(directory / "corrections.jsonl")[-1]["manual_evidence"][0][
            "evidence_id"
        ]
        runtime.set_pipeline_runner(main._run_pipeline)
        body = CorrectionRequest(
            analysis_basis_token=basis,
            reason="correct numeric value",
            changes=[
                {
                    "target_type": "fact",
                    "target_id": target,
                    "field": "raw_value",
                    "value": str(value),
                    "evidence_ids": [evidence],
                }
            ],
        )
        previous = asyncio.all_tasks()
        await create_job_correction(job_id, body, request)
        spawned = asyncio.all_tasks() - previous
        if spawned:
            await asyncio.gather(*spawned)

    decision("confirmed", "Original version decision")
    capture("01_current")
    asyncio.run(correct(90))
    capture("02_current")
    capture("03_historical", revision)
    asyncio.run(correct(110))
    decision("no_issue", "Later version decision")
    capture("04_historical", revision)
    capture("05_current")
    for suffix in ("json", "csv", "docx", "pdf"):
        assert (tmp_path / f"03_historical/report.{suffix}").read_bytes() == (
            tmp_path / f"04_historical/report.{suffix}"
        ).read_bytes()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
    gate = _evaluate_export_agreement({"documents": sets}, tmp_path)
    assert gate["consistent_documents"] == gate["comparable_documents"] == 5

    pristine = tmp_path / "01_current/report.docx"
    with zipfile.ZipFile(pristine) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    xml = entries["word/document.xml"].decode()
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    tree = ET.fromstring(xml)
    for table in tree.findall(".//w:tbl", ns):
        for row in list(table.findall("w:tr", ns)):
            if "DECISION_ID|" in "".join(row.itertext()):
                table.remove(row)
    mutations = {
        "deleted_decision": ET.tostring(tree),
        "changed_reason": xml.replace("rule_evidence_incomplete", "tampered_reason").encode(),
        "changed_revision": xml.replace(revision, "tampered_revision").encode(),
        "changed_message": xml.replace("文本收入预算与T1不一致", "tampered_message").encode(),
    }
    for name, changed in mutations.items():
        assert changed != entries["word/document.xml"]
        mutated = tmp_path / f"{name}.docx"
        with zipfile.ZipFile(mutated, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for key, content in entries.items():
                archive.writestr(key, changed if key == "word/document.xml" else content)
        row = {
            **sets[0],
            "export_paths": [
                str(mutated) if Path(p).suffix == ".docx" else p for p in sets[0]["export_paths"]
            ],
        }
        rejected = _evaluate_export_agreement({"documents": [row]}, tmp_path)
        assert rejected["status"] == "not_computable", (name, rejected)
        assert rejected.get("consistent_documents", 0) == 0, (name, rejected)
