from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.engine.rules_v33 import Issue, build_document
from src.schemas.issues import AnalysisConfig, IssueItem, JobContext
from src.services.ai_findings import AIFindingsService
from src.services.engine_rule_runner import EngineRuleRunner
from src.utils.issue_bbox import PDFBBoxLocator


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


def test_pdf_bbox_locator_finds_bbox_from_structured_row(tmp_path: Path) -> None:
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

    assert updated.bbox is not None
    assert updated.evidence[0]["bbox"] == updated.bbox
    assert updated.location["table_refs"][0]["bbox"] == updated.bbox


def test_pdf_bbox_locator_populates_each_table_ref_bbox(tmp_path: Path) -> None:
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
    assert refs[0]["bbox"] is not None
    assert refs[1]["bbox"] is not None
    assert refs[0]["page"] == 1
    assert refs[1]["page"] == 2
    assert updated.bbox == refs[0]["bbox"]
    assert updated.evidence[0]["bbox"] == refs[0]["bbox"]


def test_pdf_bbox_locator_uses_row_field_intersection_for_table_cells(tmp_path: Path) -> None:
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

    assert updated.bbox is not None
    assert updated.evidence[0]["bbox"] == updated.bbox
    assert updated.location["table_refs"][0]["bbox"] is not None


def test_pdf_bbox_locator_expands_budget_table_code_to_alias_terms() -> None:
    locator = PDFBBoxLocator("missing.pdf")
    terms = locator._expand_table_terms("BUD_T3")
    assert "BUD_T3" in terms
    assert any("支出预算总表" in term for term in terms)


@pytest.mark.asyncio
async def test_engine_rule_runner_populates_bbox_for_rule_findings(tmp_path: Path) -> None:
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
    assert finding.bbox is not None
    assert finding.evidence[0]["bbox"] == finding.bbox
