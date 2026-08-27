import sys
from pathlib import Path
from typing import Any, Dict, List
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


# ---------------------------------------------------------------------------
# hits 转换：必需字段与 span 形状校验
#
# 这一组针对的是一个真实缺陷：原实现在内层 `for span_field` 循环里 `continue`，
# 那只是跳到下一个 span 字段，形状不对的 hit 依然会走到 `converted.append(hit)`。
# 日志写着"跳过span格式错误的hit"，实际却放行了——日志与行为相反。
# ---------------------------------------------------------------------------
def _valid_hit(**overrides: Any) -> Dict[str, Any]:
    hit: Dict[str, Any] = {
        "budget_text": "40.00",
        "budget_span": [0, 5],
        "final_text": "41.00",
        "final_span": [6, 11],
        "stmt_text": "预算与决算对比",
        "stmt_span": [12, 19],
        "clip": {"page": 1},
    }
    hit.update(overrides)
    return hit


def _convert(hits: List[Any]) -> List[Dict[str, Any]]:
    return ExtractorClient()._convert_hits_to_internal_format(hits)


def test_convert_keeps_valid_hit() -> None:
    """正例：三个 span 都合法时原样保留（证明下面的丢弃不是全量误杀）。"""
    converted = _convert([_valid_hit()])
    assert len(converted) == 1
    assert converted[0]["budget_span"] == [0, 5]
    assert converted[0]["stmt_text"] == "预算与决算对比"


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("budget_span", "0-5"),  # 不是 list
        ("final_span", [0, 5, 9]),  # 长度不是 2
        ("stmt_span", None),  # 空值
        ("budget_span", []),  # 空列表
        ("final_span", {"start": 0, "end": 5}),  # dict 而不是 list
    ],
)
def test_convert_skips_hit_with_invalid_span(field: str, bad_value: Any) -> None:
    """span 是必需字段，形状不对就整条丢弃。

    取舍：形状不对说明这条 AI 响应违反了约定，同一条响应里的其它字段同样不可信，
    因此不做"部分采纳"。可选的 reason_span 才走"置空但保留"。
    """
    converted = _convert([_valid_hit(**{field: bad_value})])
    assert converted == [], f"{field}={bad_value!r} 的 hit 不应进入结果"


def test_convert_bad_span_does_not_drop_sibling_hits() -> None:
    """反向对照：只丢坏的那条，前后的好 hit 必须留下。

    没有这条，把 `continue` 误写成 `break` 也能让上面的测试变绿——那会把坏 hit
    之后的所有 hit 一起丢掉，是比原缺陷更严重的漏检。
    """
    converted = _convert(
        [
            _valid_hit(clip={"page": 1}),
            _valid_hit(stmt_span="x", clip={"page": 2}),
            _valid_hit(clip={"page": 3}),
        ]
    )
    assert [item["clip"]["page"] for item in converted] == [1, 3]


def test_convert_skips_hit_missing_required_field() -> None:
    """缺必需字段同样整条丢弃（与 span 分支保持一致）。"""
    hit = _valid_hit()
    hit.pop("clip")
    assert _convert([hit]) == []


def test_convert_nulls_invalid_reason_span_but_keeps_hit() -> None:
    """可选字段的处置刻意不同：reason_span 形状不对只置空，不丢整条。"""
    converted = _convert([_valid_hit(reason_span="0-3", reason_text="因公出国费减少")])
    assert len(converted) == 1
    assert converted[0]["reason_span"] is None
    assert converted[0]["reason_text"] is None
    # 其它字段不受影响
    assert converted[0]["budget_span"] == [0, 5]


def test_convert_keeps_valid_reason_span() -> None:
    """反向对照：合法的 reason_span 不能被顺手清掉。"""
    converted = _convert([_valid_hit(reason_span=[2, 8], reason_text="因公出国费减少")])
    assert converted[0]["reason_span"] == [2, 8]
    assert converted[0]["reason_text"] == "因公出国费减少"
