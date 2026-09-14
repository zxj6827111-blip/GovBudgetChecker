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


def test_pdf_bbox_locator_prefers_rule_anchor_over_cross_line_evidence(tmp_path: Path) -> None:
    """V33-001 年度缺位回归：规则给出的精确锚词必须优先于跨行拼接的 evidence 切分词。

    旧实现把 evidence_text 按标点切词后在页面上全文搜索，跨行拼接出的碎片词
    （如「收入支出决算总体情况说明」）可能先在别的行命中，实测把「202 年度」
    的问题标到了「一、收入支出决算总体情况说明」这一行。location 增加 anchor
    字段（命中文本本身）后，必须精确命中 anchor 所在行。
    """
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

    assert updated.bbox is not None
    # 目标行 y≈72（_create_pdf 第一行起点），干扰行 y≈90（第二行起点）。
    # bbox 的 y0 必须落在目标行附近，而不是被切分词带到干扰行。
    assert updated.bbox[1] < 80, f"bbox 应命中「202 年度」所在行，实际落在 {updated.bbox}"


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
