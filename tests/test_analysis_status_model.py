"""分析结论枚举与任务状态模型测试（Task 1，纯基础层，无行为变更）。

断言意图：
1. 四态结论与任务态取值锁定，防止后续 Task 改字符串导致前后端口径漂移；
2. 历史写法（done/completed/degraded/failed/running...）归一后不丢语义；
3. 无法识别的状态返回 None，而不是兜底成某个确定结论；
4. 没有 analysis_conclusion 字段的旧任务能被正确反推，保证向后兼容。
"""

import pytest

from api import runtime
from src.schemas.issues import (
    ACTIVE_JOB_STATUSES,
    COMPLETED_JOB_STATUSES,
    TERMINAL_JOB_STATUSES,
    AnalysisConclusion,
    AnalysisQualityStatus,
    JobStatus,
    infer_analysis_conclusion,
    normalize_job_status,
)


def test_analysis_conclusion_values_are_locked():
    assert {item.value for item in AnalysisConclusion} == {
        "findings_detected",
        "no_findings",
        "incomplete",
        "analysis_error",
    }


def test_job_status_values_include_review_required():
    assert {item.value for item in JobStatus} == {
        "queued",
        "processing",
        "done",
        "degraded",
        "review_required",
        "error",
    }
    # review_required 必须是终态且属于"分析跑完"的一类，否则前端会当成还在跑
    assert JobStatus.REVIEW_REQUIRED.value in TERMINAL_JOB_STATUSES
    assert JobStatus.REVIEW_REQUIRED.value in COMPLETED_JOB_STATUSES


def test_quality_status_values_are_locked():
    assert {item.value for item in AnalysisQualityStatus} == {
        "complete",
        "degraded",
        "review_required",
    }


def test_status_sets_do_not_overlap():
    assert ACTIVE_JOB_STATUSES & TERMINAL_JOB_STATUSES == frozenset()
    assert JobStatus.ERROR.value not in COMPLETED_JOB_STATUSES


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 旧任务的两种终态必须原样保留语义
        ("done", "done"),
        ("degraded", "degraded"),
        # 前端/历史别名
        ("completed", "done"),
        ("SUCCESS", "done"),
        ("failed", "error"),
        ("cancelled", "error"),
        ("running", "processing"),
        ("analyzing", "processing"),
        ("pending", "queued"),
        ("  Review_Required  ", "review_required"),
    ],
)
def test_normalize_job_status_maps_legacy_aliases(raw, expected):
    assert normalize_job_status(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "unknown_state", 123])
def test_normalize_job_status_returns_none_for_unrecognized(raw):
    assert normalize_job_status(raw) is None


def test_infer_conclusion_prefers_explicit_value():
    # 已经写入结论的任务不被反推逻辑覆盖
    assert (
        infer_analysis_conclusion("done", issue_total=0, explicit_conclusion="incomplete")
        == AnalysisConclusion.INCOMPLETE.value
    )


def test_infer_conclusion_ignores_invalid_explicit_value():
    assert (
        infer_analysis_conclusion("done", issue_total=3, explicit_conclusion="garbage")
        == AnalysisConclusion.FINDINGS_DETECTED.value
    )


@pytest.mark.parametrize(
    ("status", "issue_total", "expected"),
    [
        # 向后兼容：历史 done/degraded 任务仍能读出有意义的结论
        ("done", 0, AnalysisConclusion.NO_FINDINGS.value),
        ("done", 5, AnalysisConclusion.FINDINGS_DETECTED.value),
        ("degraded", 0, AnalysisConclusion.NO_FINDINGS.value),
        ("degraded", 2, AnalysisConclusion.FINDINGS_DETECTED.value),
        ("completed", 1, AnalysisConclusion.FINDINGS_DETECTED.value),
        ("error", 0, AnalysisConclusion.ANALYSIS_ERROR.value),
        ("failed", 9, AnalysisConclusion.ANALYSIS_ERROR.value),
        # review_required 无论有多少问题，结论都是"不完整"
        ("review_required", 0, AnalysisConclusion.INCOMPLETE.value),
        ("review_required", 7, AnalysisConclusion.INCOMPLETE.value),
    ],
)
def test_infer_conclusion_from_status(status, issue_total, expected):
    assert infer_analysis_conclusion(status, issue_total=issue_total) == expected


@pytest.mark.parametrize("status", ["queued", "processing", "running", "unknown_state", None])
def test_infer_conclusion_is_none_when_not_terminal_or_unknown(status):
    assert infer_analysis_conclusion(status, issue_total=3) is None


def test_job_status_context_keys_cover_quality_fields():
    for key in ("analysis_conclusion", "quality_status", "page_coverage", "scanned_page_count"):
        assert key in runtime.JOB_STATUS_CONTEXT_KEYS


def test_extract_job_status_context_carries_quality_fields():
    payload = {
        "filename": "sample.pdf",
        "analysis_conclusion": "incomplete",
        "quality_status": "review_required",
        "page_coverage": 0.25,
        "scanned_page_count": 3,
    }
    context = runtime.extract_job_status_context(payload)
    assert context["analysis_conclusion"] == "incomplete"
    assert context["quality_status"] == "review_required"
    assert context["page_coverage"] == 0.25
    assert context["scanned_page_count"] == 3


def test_extract_job_status_context_skips_missing_quality_fields():
    # 旧任务没有这些字段时不应被塞入 None，避免污染 status.json
    context = runtime.extract_job_status_context({"filename": "legacy.pdf"})
    assert context == {"filename": "legacy.pdf"}
