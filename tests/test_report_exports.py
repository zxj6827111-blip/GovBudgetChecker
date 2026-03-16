import csv
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api import runtime


def _create_pdf(path: Path, lines_per_page: list[list[str]]) -> None:
    fitz = pytest.importorskip("fitz")
    document = fitz.open()
    try:
        for lines in lines_per_page:
            page = document.new_page()
            y = 72
            for line in lines:
                page.insert_text((72, y), line, fontsize=12)
                y += 18
        document.save(path)
    finally:
        document.close()


def _write_status(job_dir: Path, payload: dict) -> None:
    (job_dir / "status.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def test_report_download_pdf_contains_multi_ref_labels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fitz = pytest.importorskip("fitz")
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)

    job_id = "job-export-pdf"
    job_dir = tmp_path / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    source_pdf = job_dir / "source.pdf"
    _create_pdf(
        source_pdf,
        [
            ["Narrative Ref", "FIELD_ALPHA 100"],
            ["Budget Ref", "FIELD_BETA 90"],
        ],
    )

    _write_status(
        job_dir,
        {
            "job_id": job_id,
            "status": "done",
            "saved_path": f"{job_id}/source.pdf",
            "result": {
                "rule_findings": [
                    {
                        "id": "rule-export-1",
                        "source": "rule",
                        "rule_id": "TEST-EXPORT",
                        "severity": "high",
                        "title": "Cross-table mismatch",
                        "message": "Cross-table mismatch",
                        "location": {
                            "page": 1,
                            "pages": [1, 2],
                            "table_refs": [
                                {
                                    "role": "REF-A",
                                    "page": 1,
                                    "section": "Narrative Ref",
                                    "field": "FIELD_ALPHA",
                                    "bbox": [72, 61, 150, 79],
                                },
                                {
                                    "role": "REF-B",
                                    "page": 2,
                                    "table": "Budget Ref",
                                    "field": "FIELD_BETA",
                                    "bbox": [72, 61, 140, 79],
                                },
                            ],
                        },
                        "bbox": [72, 61, 150, 79],
                        "evidence": [{"page": 1, "text": "FIELD_ALPHA 100"}],
                        "metrics": {},
                        "tags": [],
                    }
                ]
            },
        },
    )

    client = TestClient(app)
    response = client.get(f"/api/reports/download?job_id={job_id}")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")

    with fitz.open(stream=response.content, filetype="pdf") as exported:
        page1_text = exported[0].get_text("text")
        page2_text = exported[1].get_text("text")

    assert "REF-A" in page1_text
    assert "REF-B" in page2_text
    assert "TEST-EXPORT" in page1_text
    assert "问题索引" in page1_text


def test_report_download_json_and_csv_include_structured_location_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)

    job_id = "job-export-structured"
    job_dir = tmp_path / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    _create_pdf(job_dir / "source.pdf", [["Table A", "ROW_A 100"]])

    _write_status(
        job_dir,
        {
            "job_id": job_id,
            "status": "done",
            "saved_path": f"{job_id}/source.pdf",
            "result": {
                "rule_findings": [
                    {
                        "id": "rule-export-2",
                        "source": "rule",
                        "rule_id": "TEST-STRUCTURED",
                        "severity": "medium",
                        "title": "Structured location export",
                        "message": "Structured location export",
                        "location": {
                            "page": 1,
                            "pages": [1, 2],
                            "table": "Table A",
                            "row": "ROW_A",
                            "field": "Field A",
                            "code": "201999",
                            "subject": "Subject A",
                            "expected_name": "\u536b\u751f\u5065\u5eb7\u652f\u51fa",
                            "actual_name": "\u533b\u7597\u536b\u751f\u4e0e\u8ba1\u5212\u751f\u80b2\u652f\u51fa",
                            "code_level": "\u7c7b",
                            "source_of_truth": "BUD_T5",
                            "table_refs": [
                                {
                                    "role": "T1",
                                    "page": 1,
                                    "table": "Table A",
                                    "row": "ROW_A",
                                    "field": "Field A",
                                    "bbox": [72, 61, 135, 79],
                                }
                            ],
                        },
                        "bbox": [72, 61, 135, 79],
                        "evidence": [{"page": 1, "text": "ROW_A 100", "bbox": [72, 61, 135, 79]}],
                        "metrics": {},
                        "tags": [],
                        "suggestion": "Check source values",
                    }
                ]
            },
        },
    )

    client = TestClient(app)

    report_json = client.get(f"/api/reports/download?job_id={job_id}&format=json")
    assert report_json.status_code == 200
    payload = report_json.json()
    issue = payload["issues"][0]
    assert issue["severity_label"] == "中"
    export_location = issue["export_location"]
    assert export_location["page"] == 1
    assert export_location["table"] == "Table A"
    assert export_location["row"] == "ROW_A"
    assert export_location["field"] == "Field A"
    assert export_location["code"] == "201999"
    assert export_location["subject"] == "Subject A"
    assert export_location["role_summary"] == "T1/P1/表:Table A"
    assert export_location["table_refs"][0]["bbox"] == [72.0, 61.0, 135.0, 79.0]

    report_csv = client.get(f"/api/reports/download?job_id={job_id}&format=csv")
    assert report_csv.status_code == 200
    text = report_csv.content.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert rows
    row = rows[0]
    assert "规则编号" in row
    assert "严重级别" in row
    assert "table_refs" in row
    assert row["rule_id"] == "TEST-STRUCTURED"
    assert row["severity_label"] == "中"
    assert row["严重级别"] == "中"
    assert row["严重级别代码"] == "medium"
    assert row["规则编号"] == "TEST-STRUCTURED"
    assert row["table"] == "Table A"
    assert row["表"] == "Table A"
    assert row["row"] == "ROW_A"
    assert row["行"] == "ROW_A"
    assert row["field"] == "Field A"
    assert row["字段"] == "Field A"
    assert row["table_ref_count"] == "1"
    assert row["evidence_role_summary"] == "T1/P1/表:Table A"
    table_refs = json.loads(row["table_refs"])
    assert table_refs[0]["role"] == "T1"
    assert table_refs[0]["bbox"] == [72.0, 61.0, 135.0, 79.0]


def test_report_download_uses_merged_issue_projection_without_double_counting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)

    job_id = "job-export-dual-merged"
    job_dir = tmp_path / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    _create_pdf(job_dir / "source.pdf", [["Alpha"], ["Beta"]])

    rule_issue = {
        "id": "rule-export-3",
        "source": "rule",
        "rule_id": "RULE-3",
        "severity": "medium",
        "title": "Rule issue",
        "message": "Rule issue",
        "location": {"page": 2},
        "evidence": [{"page": 2, "text": "Beta"}],
        "metrics": {},
        "tags": [],
    }
    ai_issue = {
        "id": "ai-export-1",
        "source": "ai",
        "rule_id": "AI-1",
        "severity": "high",
        "title": "AI issue",
        "message": "AI issue",
        "location": {"page": 1},
        "evidence": [{"page": 1, "text": "Alpha"}],
        "metrics": {},
        "tags": [],
    }

    _write_status(
        job_dir,
        {
            "job_id": job_id,
            "status": "done",
            "saved_path": f"{job_id}/source.pdf",
            "result": {
                "issues": {
                    "error": [],
                    "warn": [rule_issue],
                    "info": [],
                    "all": [rule_issue],
                },
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
                    "merged_ids": ["ai-export-1", "rule-export-3"],
                    "conflicts": [],
                    "agreements": [],
                },
            },
        },
    )

    client = TestClient(app)

    report_json = client.get(f"/api/reports/download?job_id={job_id}&format=json")
    assert report_json.status_code == 200
    payload = report_json.json()
    assert payload["count"] == 2
    assert [item["id"] for item in payload["issues"]] == ["ai-export-1", "rule-export-3"]

    report_csv = client.get(f"/api/reports/download?job_id={job_id}&format=csv")
    assert report_csv.status_code == 200
    text = report_csv.content.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == 2
    assert [row["rule_id"] for row in rows] == ["AI-1", "RULE-3"]
    assert [row["严重级别"] for row in rows] == ["高", "中"]
