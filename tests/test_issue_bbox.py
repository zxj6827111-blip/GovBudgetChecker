from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.engine.rules_v33 import Issue, build_document
from src.schemas.issues import AnalysisConfig, IssueItem, JobContext
from src.services.ai_findings import AIFindingsService
from src.services.engine_rule_runner import EngineRuleRunner
from src.utils.issue_bbox import PDFBBoxLocator


def _create_pdf(path: Path, lines_per_page: list[list[str]], *, fontname: str = "helv") -> None:
    fitz = pytest.importorskip("fitz")
    document = fitz.open()
    try:
        for lines in lines_per_page:
            page = document.new_page()
            y = 72
            for line in lines:
                page.insert_text((72, y), line, fontsize=12, fontname=fontname)
                y += 18
        document.save(path)
    finally:
        document.close()


def test_pdf_bbox_locator_does_not_infer_bbox_from_structured_row(tmp_path: Path) -> None:
    pdf_path = tmp_path / "sample.pdf"
    _create_pdf(
        pdf_path,
        [["Test Table", "ROW_KEY 100", "Other content"]],
    )

    item = IssueItem(
        id="rule:v33-200:bbox",
        source="rule",
        rule_id="TEST-BBOX",
        severity="high",
        title="Synthetic bbox test",
        message="Synthetic bbox test",
        evidence=[{"page": 1, "text": "ROW_KEY 100"}],
        location={
            "page": 1,
            "table": "Test Table",
            "row": "ROW_KEY",
            "field": "ROW_KEY",
            "table_refs": [
                {
                    "role": "primary",
                    "page": 1,
                    "table": "Test Table",
                    "row": "ROW_KEY",
                    "field": "ROW_KEY",
                }
            ],
        },
        metrics={},
        tags=[],
    )

    locator = PDFBBoxLocator(str(pdf_path))
    try:
        updated = locator.locate(item)
    finally:
        locator.close()

    assert updated.bbox is None
    assert updated.evidence[0].get("bbox") is None
    assert updated.location["table_refs"][0].get("bbox") is None
    assert updated.location_level == "region"
    assert updated.verification_status == "unverified"


def test_pdf_bbox_locator_keeps_refs_as_region_navigation_without_source_boxes(tmp_path: Path) -> None:
    pdf_path = tmp_path / "multi-ref.pdf"
    _create_pdf(
        pdf_path,
        [
            ["Narrative Section", "FIELD_ALPHA 100", "More content"],
            ["Budget Table", "FIELD_BETA 90", "Other content"],
        ],
    )

    item = IssueItem(
        id="rule:v33-201:multi-bbox",
        source="rule",
        rule_id="TEST-MULTI-BBOX",
        severity="high",
        title="Synthetic multi bbox test",
        message="Synthetic multi bbox test",
        evidence=[{"page": 1, "text": "FIELD_ALPHA 100"}],
        location={
            "page": 1,
            "pages": [1, 2],
            "table_refs": [
                {
                    "role": "说明4",
                    "page": 1,
                    "section": "Narrative Section",
                    "field": "FIELD_ALPHA",
                },
                {
                    "role": "T4",
                    "page": 2,
                    "table": "Budget Table",
                    "field": "FIELD_BETA",
                },
            ],
        },
        metrics={},
        tags=[],
    )

    locator = PDFBBoxLocator(str(pdf_path))
    try:
        updated = locator.locate(item)
    finally:
        locator.close()

    refs = updated.location["table_refs"]
    assert len(refs) == 2
    assert refs[0].get("bbox") is None
    assert refs[1].get("bbox") is None
    assert refs[0]["page"] == 1
    assert refs[1]["page"] == 2
    assert updated.bbox is None
    assert updated.evidence[0].get("bbox") is None
    assert all(ref["location_level"] == "region" for ref in refs)


def test_pdf_bbox_locator_does_not_infer_table_cell_bbox(tmp_path: Path) -> None:
    pdf_path = tmp_path / "budget-cell.pdf"
    _create_pdf(
        pdf_path,
        [
            [
                "Budget Table",
                "ITEM TOTAL BASIC PROJECT",
                "TOTAL 120.00 80.00 40.00",
            ]
        ],
    )

    item = IssueItem(
        id="rule:test:cell-bbox",
        source="rule",
        rule_id="TEST-CELL-BBOX",
        severity="medium",
        title="Synthetic table cell bbox",
        message="Synthetic table cell bbox",
        evidence=[{"page": 1, "text": "Narrative evidence outside the table"}],
        location={
            "page": 1,
            "table": "Budget Table",
            "row": "TOTAL",
            "field": "PROJECT",
            "table_refs": [
                {
                    "role": "primary",
                    "page": 1,
                    "table": "Budget Table",
                    "row": "TOTAL",
                    "field": "PROJECT",
                }
            ],
        },
        metrics={},
        tags=[],
    )

    locator = PDFBBoxLocator(str(pdf_path))
    try:
        updated = locator.locate(item)
    finally:
        locator.close()

    assert updated.bbox is None
    assert updated.evidence[0].get("bbox") is None
    assert updated.location["table_refs"][0].get("bbox") is None


def test_pdf_bbox_locator_preserves_verified_exact_bbox_within_page(tmp_path: Path) -> None:
    pdf_path = tmp_path / "verified.pdf"
    _create_pdf(pdf_path, [["Verified source text"]])
    item = IssueItem(
        id="rule:test:verified-bbox",
        source="rule",
        rule_id="TEST-BBOX",
        severity="high",
        title="Verified bbox",
        message="Verified bbox",
        evidence=[{"page": 1, "text": "Verified source text"}],
        location={"page": 1},
        bbox=[70, 60, 220, 80],
        location_level="exact_span",
        verification_status="verified",
    )

    locator = PDFBBoxLocator(str(pdf_path))
    try:
        updated = locator.locate(item)
    finally:
        locator.close()

    assert updated.bbox == [70.0, 60.0, 220.0, 80.0]
    assert updated.location_level == "exact_span"
    assert updated.verification_status == "verified"
    assert updated.evidence[0].get("bbox") is None


def test_pdf_bbox_locator_promotes_source_verified_evidence_bbox(tmp_path: Path) -> None:
    pdf_path = tmp_path / "verified-evidence.pdf"
    _create_pdf(pdf_path, [["Verified source text"]])
    item = IssueItem(
        id="rule:test:verified-evidence-bbox",
        source="rule",
        rule_id="TEST-BBOX",
        severity="high",
        title="Verified evidence bbox",
        message="Verified evidence bbox",
        evidence=[
            {
                "page": 1,
                "text": "Verified source text",
                "bbox": [70, 60, 220, 80],
                "location_level": "exact_span",
                "verification_status": "verified",
            }
        ],
        location={"page": 1},
    )

    locator = PDFBBoxLocator(str(pdf_path))
    try:
        updated = locator.locate(item)
    finally:
        locator.close()

    assert updated.bbox == [70.0, 60.0, 220.0, 80.0]
    assert updated.evidence[0]["bbox"] == updated.bbox
    assert updated.location_level == "exact_span"
    assert updated.verification_status == "verified"


def test_pdf_bbox_locator_rejects_out_of_range_verified_bbox(tmp_path: Path) -> None:
    pdf_path = tmp_path / "verified-out-of-range.pdf"
    _create_pdf(pdf_path, [["Only one page"]])
    item = IssueItem(
        id="rule:test:out-of-range-bbox",
        source="rule",
        rule_id="TEST-BBOX",
        severity="high",
        title="Out of range bbox",
        message="Out of range bbox",
        evidence=[{"page": 2, "text": "Only one page"}],
        location={"page": 2},
        bbox=[70, 60, 220, 80],
        location_level="exact_span",
        verification_status="verified",
    )

    locator = PDFBBoxLocator(str(pdf_path))
    try:
        updated = locator.locate(item)
    finally:
        locator.close()

    assert updated.bbox is None
    assert updated.location_level == "unresolved"
    assert updated.verification_status == "unverified"


def test_pdf_bbox_locator_keeps_anchor_finding_unresolved_without_source_span(tmp_path: Path) -> None:
    pdf_path = tmp_path / "anchor.pdf"
    # 目标行（含「202 年度」）在前（y≈72），干扰行在后（y≈90）。
    # 中文必须用内置 CJK 字体写入，默认 helv 会把中文字符丢弃导致 search_for 落空。
    _create_pdf(
        pdf_path,
        [
            ["第三部分 上海市普陀区生态环境局 202 年度部门决算情况说明"],
            ["一、收入支出决算总体情况说明"],
        ],
        fontname="china-s",
    )

    evidence_text = (
        "算表 第三部分 上海市普陀区生态环境局 202 年度部门决算情况说明 "
        "一、收入支出决算总体情况说明 二、收入决算"
    )
    item = IssueItem(
        id="rule:v33-001:anchor",
        source="rule",
        rule_id="V33-001",
        severity="high",
        title="目录/封面年度缺位：「202 年度」疑似应为完整年份",
        message="目录/封面年度缺位：「202 年度」疑似应为完整年份（如 2025 年度）。",
        evidence=[{"page": 1, "text": evidence_text}],
        location={"page": 1, "pos": 42, "anchor": "202 年度"},
        metrics={},
        tags=[],
    )

    locator = PDFBBoxLocator(str(pdf_path))
    try:
        updated = locator.locate(item)
    finally:
        locator.close()

    assert updated.bbox is None
    assert updated.location_level == "unresolved"


@pytest.mark.asyncio
async def test_engine_rule_runner_keeps_rule_findings_unlocated_without_source_bbox(tmp_path: Path) -> None:
    pdf_path = tmp_path / "runner.pdf"
    _create_pdf(
        pdf_path,
        [
            ["Cover"],
            ["Test Table", "ROW_KEY 100"],
        ],
    )

    doc = build_document(
        path=str(pdf_path),
        page_texts=[
            "Cover",
            "Test Table\nROW_KEY 100",
        ],
        page_tables=[
            [],
            [[["Item", "Value"], ["ROW_KEY", "100"]]],
        ],
        filesize=pdf_path.stat().st_size,
    )

    class _DummyRule:
        code = "TEST-BBOX"
        desc = "dummy"

        def apply(self, _doc):
            return [
                Issue(
                    rule="TEST-BBOX",
                    severity="error",
                    message="Synthetic bbox finding",
                    evidence_text="ROW_KEY 100",
                    location={"page": 2, "table": "Test Table", "row": "ROW_KEY", "field": "ROW_KEY"},
                )
            ]

    runner = EngineRuleRunner()

    async def _fake_prepare(_job_context):
        return doc

    runner._prepare_document = _fake_prepare  # type: ignore[method-assign]
    runner._select_rule_set = lambda _job_context, _document: [_DummyRule()]  # type: ignore[method-assign]

    findings = await runner.run_rules(
        job_context=JobContext(
            job_id="job-bbox",
            pdf_path=str(pdf_path),
            page_texts=doc.page_texts,
            page_tables=doc.page_tables,
            meta={"report_kind": "final"},
        ),
        rules=[],
        config=AnalysisConfig(),
    )

    assert findings
    finding = findings[0]
    assert finding.bbox is None
    assert finding.evidence[0].get("bbox") is None
    assert finding.location_level == "region"


@pytest.mark.asyncio
async def test_engine_rule_runner_records_issue_conversion_failure_as_parse_error(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "runner-parse-error.pdf"
    _create_pdf(pdf_path, [["Cover"]])
    doc = build_document(
        path=str(pdf_path),
        page_texts=["Cover"],
        page_tables=[[]],
        filesize=pdf_path.stat().st_size,
    )

    class _DummyRule:
        code = "TEST-PARSE"
        desc = "dummy"

        def apply(self, _doc):
            return [
                Issue(
                    rule="TEST-PARSE",
                    severity="error",
                    message="Synthetic conversion failure",
                    location={"page": 1},
                )
            ]

    runner = EngineRuleRunner()

    async def _fake_prepare(_job_context):
        return doc

    runner._prepare_document = _fake_prepare  # type: ignore[method-assign]
    runner._select_rule_set = lambda _job_context, _document: [_DummyRule()]  # type: ignore[method-assign]

    def _raise_conversion(*_args, **_kwargs):
        raise ValueError("invalid finding")

    runner._issue_to_finding = _raise_conversion  # type: ignore[method-assign]
    findings = await runner.run_rules(
        job_context=JobContext(
            job_id="job-parse-error",
            pdf_path=str(pdf_path),
            page_texts=doc.page_texts,
            page_tables=doc.page_tables,
            meta={"report_kind": "final"},
        ),
        rules=[],
        config=AnalysisConfig(),
    )

    assert findings == []
    summary = runner.get_rule_execution_summary()
    assert summary["parse_error"] == 1
    assert summary["pass"] == 0
