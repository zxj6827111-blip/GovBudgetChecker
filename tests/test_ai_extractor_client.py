import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.engine.ai.extractor_client import ExtractorClient


def test_rounding_issue_filter_drops_small_wanyuan_diff() -> None:
    item = {
        "problem_type": "DataInconsistency",
        "message": "金额不一致",
    }
    dropped = ExtractorClient._should_drop_rounding_issue(
        item=item,
        issue_type="勾稽不一致",
        original="项目支出 40.00万元",
        suggestion="项目支出 40.01万元",
        context="金额差异仅由四舍五入导致",
    )
    assert dropped


def test_rounding_issue_filter_keeps_material_diff() -> None:
    item = {
        "problem_type": "DataInconsistency",
        "message": "金额不一致",
    }
    dropped = ExtractorClient._should_drop_rounding_issue(
        item=item,
        issue_type="勾稽不一致",
        original="项目支出 40.00万元",
        suggestion="项目支出 42.00万元",
        context="金额差异较大",
    )
    assert not dropped


def test_rounding_issue_filter_does_not_drop_year_mismatch() -> None:
    item = {
        "problem_type": "DataInconsistency",
        "message": "年份不一致",
    }
    dropped = ExtractorClient._should_drop_rounding_issue(
        item=item,
        issue_type="规范性",
        original="2025年部门预算",
        suggestion="2026年部门预算",
        context="年份口径不一致",
    )
    assert not dropped


def test_repeat_issue_filter_drops_single_occurrence() -> None:
    text = "这是预算说明文本。A段落只出现一次，且包含较长的描述内容用于检测。"
    dropped = ExtractorClient._should_drop_unverified_repeat_issue(
        section_text=text,
        issue_type="repeated_content",
        original="A段落只出现一次，且包含较长的描述内容用于检测。",
        context="",
    )
    assert dropped


def test_repeat_issue_filter_keeps_real_repeat() -> None:
    text = "预算说明：B段落重复。预算说明：B段落重复。"
    dropped = ExtractorClient._should_drop_unverified_repeat_issue(
        section_text=text,
        issue_type="重复",
        original="预算说明：B段落重复。",
        context="",
    )
    assert not dropped


def test_response_content_text_supports_list_payload() -> None:
    payload = [{"content": "[{\"type\":\"规范性\"}]"}]
    text = ExtractorClient._response_content_text(payload)
    assert text == "[{\"type\":\"规范性\"}]"


def test_response_content_text_supports_dict_payload() -> None:
    payload = {"content": "[]"}
    text = ExtractorClient._response_content_text(payload)
    assert text == "[]"


def test_normalize_confidence_accepts_percent_and_number() -> None:
    assert ExtractorClient._normalize_confidence("82%") == 0.82
    assert ExtractorClient._normalize_confidence("0.91") == 0.91
    assert ExtractorClient._normalize_confidence(1) == 1.0


def test_normalize_confidence_rejects_non_numeric_text() -> None:
    assert ExtractorClient._normalize_confidence("high") is None
    assert ExtractorClient._normalize_confidence("") is None
    assert ExtractorClient._normalize_confidence(None) is None


def test_normalize_severity_maps_p_levels() -> None:
    assert ExtractorClient._normalize_severity("p0") == "high"
    assert ExtractorClient._normalize_severity("p1") == "medium"
    assert ExtractorClient._normalize_severity("p2") == "low"
    assert ExtractorClient._normalize_severity("manual_review") == "manual_review"
    assert ExtractorClient._normalize_severity("unknown") == "medium"


@pytest.mark.asyncio
async def test_full_report_audit_falls_back_when_direct_result_is_empty() -> None:
    client = ExtractorClient()
    client._direct_semantic_audit = AsyncMock(return_value=[])
    client.ai_semantic_audit = AsyncMock(return_value=[{"type": "should_not_run"}])

    result = await client.ai_full_report_audit("测试文本", "doc-hash")

    assert result == [{"type": "should_not_run"}]
    client._direct_semantic_audit.assert_awaited_once()
    client.ai_semantic_audit.assert_awaited_once()
