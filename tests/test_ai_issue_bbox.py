from pathlib import Path
from unittest.mock import AsyncMock
import asyncio

import pytest

from src.schemas.issues import AnalysisConfig, IssueItem, JobContext
from src.services.ai_findings import AIFindingsService
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


def test_pdf_bbox_locator_finds_bbox_from_ai_quote_text(tmp_path: Path) -> None:
    pdf_path = tmp_path / "ai-quote.pdf"
    page_text = "Performance target differs from T3 project expense: desc=43.60, T3=48.59"
    _create_pdf(pdf_path, [[page_text, "Please explain if the metrics use different scopes"]])

    item = IssueItem(
        id="ai:test:quote-bbox",
        source="ai",
        rule_id="AI-SEM-001",
        severity="high",
        title="AI quote bbox",
        message="Performance target differs from T3 project expense",
        evidence=[
            {
                "page": 1,
                "text": page_text,
                "text_snippet": page_text,
                "quote": "Performance target differs from T3 project expense",
            }
        ],
        location={"page": 1},
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


@pytest.mark.asyncio
async def test_ai_findings_service_populates_bbox_from_semantic_quote(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "ai-service.pdf"
    page_text = "Performance target differs from T3 project expense: desc=43.60, T3=48.59"
    _create_pdf(pdf_path, [[page_text]])

    service = AIFindingsService(AnalysisConfig())
    service.ai_client.ai_full_report_audit = AsyncMock(
        return_value=[
            {
                "type": "semantic_conflict",
                "title": "Performance target differs from T3 project expense",
                "message": "Performance target differs from T3 project expense",
                "quote": "Performance target differs from T3 project expense",
                "context": page_text,
                "original": page_text,
                "span": [0, len(page_text)],
                "suggestion": "Please explain if the metrics use different scopes",
            }
        ]
    )

    findings = await service.analyze(
        JobContext(
            job_id="job-ai-bbox",
            pdf_path=str(pdf_path),
            page_texts=[page_text],
            meta={},
        )
    )

    assert findings
    finding = findings[0]
    assert finding.page_number == 1
    assert finding.evidence[0]["page"] == 1
    assert finding.bbox is not None
    assert finding.evidence[0]["bbox"] == finding.bbox


@pytest.mark.asyncio
async def test_ai_findings_service_runs_window_audits_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_AUDIT_WINDOW_CHARS", "2000")
    monkeypatch.setenv("AI_AUDIT_WINDOW_OVERLAP", "200")
    monkeypatch.setenv("AI_AUDIT_MAX_WINDOWS", "4")
    monkeypatch.setenv("AI_AUDIT_MAX_CONCURRENCY", "2")

    service = AIFindingsService(AnalysisConfig())
    active = 0
    max_active = 0

    async def fake_audit(window_text: str, doc_hash: str):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.05)
        active -= 1
        return []

    service.ai_client.ai_full_report_audit = AsyncMock(side_effect=fake_audit)

    findings = await service.analyze(
        JobContext(
            job_id="job-ai-concurrency",
            pdf_path="",
            page_texts=["A" * 6500],
            meta={},
        )
    )

    assert findings == []
    assert service.ai_client.ai_full_report_audit.await_count == 4
    assert max_active >= 2
