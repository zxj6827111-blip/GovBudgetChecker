from __future__ import annotations

from typing import Any, Dict

import pytest

from api.routes.reports import (
    _append_report_appendix_pages,
    _build_export_location,
    _extract_issues,
    _normalize_table_refs,
)
from src.schemas.review_report import build_review_report


def test_zc04_review_report_is_the_shared_issue_and_receipt_source() -> None:
    finding = {
        "id": "issue-1",
        "rule_id": "C-001",
        "severity": "high",
        "title": "三公合计异常",
        "message": "表内勾稽未通过",
    }
    payload = {
        "job_id": "job-zc04",
        "status": "review_required",
        "quality_status": "review_required",
        "analysis_conclusion": "incomplete",
        "result": {
            "issues": {"all": [finding]},
            "meta": {
                "check_receipts": [
                    {
                        "rule_id": "C-001",
                        "sub_check_id": "C-001:total",
                        "status": "insufficient_data",
                        "incomplete_reason": "缺少公务接待费操作数",
                    }
                ]
            },
        },
        "document_profile": {
            "report_kind": {"value": "budget"},
            "report_year": {"value": 2025},
            "organization_name": {"value": "上海市某部门"},
        },
        "check_coverage": {"instances": [{"status": "insufficient_data", "obligation_id": "BUD-03"}]},
        "review_reasons": [{"code": "profile_uncertain", "message": "文种需复核"}],
    }

    report = build_review_report(payload)
    exported_payload = {**payload, "review_report": report}

    assert report["schema_version"] == 2
    assert report["metadata"]["year"] == 2025
    assert report["issues"] == [finding]
    assert report["check_receipts"] == payload["result"]["meta"]["check_receipts"]
    assert any(item.get("obligation_id") == "BUD-03" for item in report["manual_review"])
    assert _extract_issues(exported_payload) == report["issues"]


def test_zc04_unverified_table_ref_bbox_is_removed_from_exports() -> None:
    refs = _normalize_table_refs(
        [
            {"role": "T1", "page": 2, "table": "收支总表", "bbox": [10, 10, 40, 30]},
            {
                "role": "T2",
                "page": 3,
                "table": "收入表",
                "bbox": [20, 20, 50, 45],
                "location_level": "exact_cell",
                "verification_status": "verified",
            },
        ]
    )
    assert refs[0].get("bbox") is None
    assert refs[1]["bbox"] == [20.0, 20.0, 50.0, 45.0]

    export_location = _build_export_location(
        {
            "location": {
                "page": 2,
                "table_refs": [
                    {"role": "T1", "page": 2, "table": "收支总表", "bbox": [10, 10, 40, 30]}
                ],
            },
            "bbox": [10, 10, 40, 30],
        }
    )
    assert export_location.get("bbox") is None
    assert export_location["table_refs"][0].get("bbox") is None


def test_zc04_pdf_appendix_keeps_unlocated_issue_and_unfinished_check() -> None:
    fitz = pytest.importorskip("fitz")
    document = fitz.open()
    report_payload: Dict[str, Any] = {
        "job_id": "job-pdf-appendix",
        "status": "review_required",
        "quality_status": "review_required",
        "analysis_conclusion": "incomplete",
        "check_coverage": {"instances": [{"status": "insufficient_data", "obligation_id": "BUD-03", "title": "预算说明"}]},
        "review_report": {
            "metadata": {"report_kind": "budget", "year": 2025, "organization": "上海市某部门"},
            "obligation_coverage": {"instances": [{"status": "insufficient_data", "obligation_id": "BUD-03", "title": "预算说明"}]},
            "tips": [],
            "manual_review": [
                {"record_type": "unfinished_check", "obligation_id": "BUD-03", "title": "预算说明", "status": "insufficient_data", "reason": "缺少页内证据"}
            ],
            "check_receipts": [
                {"sub_check_id": "C-001:total", "rule_id": "C-001", "status": "insufficient_data", "incomplete_reason": "缺少分项"}
            ],
        },
    }
    issue = {
        "id": "issue-unlocated",
        "rule_id": "C-001",
        "severity": "high",
        "title": "三公合计待核对",
        "message": "已保留问题，但页码未定位",
        "location": {"location_level": "unresolved"},
        "evidence": [{"text": "原文操作数不足"}],
    }

    _append_report_appendix_pages(document, "job-pdf-appendix", report_payload, [issue])
    text = "\n".join(page.get_text() for page in document)
    document.close()

    assert "系统生成附录" in text
    assert "三公合计待核对" in text
    assert "页码：未定位" in text
    assert "BUD-03" in text
    assert "C-001:total" in text
