"""AI 检查链路整改回归测试（2026-10-25）。

覆盖：同年份不掩盖差额、元转万元精度、合法舍入、解释字段贯通、
非法/未知证据 ID、非法数组/混合/截断/超时、合法空与失败区分、
切窗残句、重叠去重、跨段双方证据、辅助定位、覆盖范围、历史兼容。
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

from src.engine.ai.audit_contract import validate_audit_items
from src.engine.ai.audit_prompts import (
    NARRATIVE_CONSISTENCY,
    SPECIALTIES,
    TEXT_SEMANTICS,
    render_specialty_instructions,
    specialty_prompt_version,
)
from src.engine.ai.extractor_client import (
    AuditIssueList,
    AuditOutcome,
    ExtractorClient,
    ExtractorConfig,
)
from src.engine.amount_math import (
    classify_amount_diff,
    classify_display_conversion,
    normalize_amount_to_wan,
)
from src.schemas.issues import AnalysisConfig, IssueItem, JobContext
from src.schemas.review_report import build_review_report
from src.services.ai_execution import (
    AI_STATE_DEGRADED,
    AI_STATE_FAILED,
    AI_STATE_SUCCEEDED,
    build_ai_execution,
    quality_gate_ai_reasons,
)
from src.providers import LLMError, LLMErrorType
from src.services.ai_findings import AIFindingsService
from src.services.ai_locator import AILocator
from src.services.ai_window_builder import TextWindow, build_text_windows
from src.engine.checklist_catalog import load_checklist


class StaticChat:
    def __init__(self, content: str, finish_reason: str = "stop") -> None:
        self.content = content
        self.finish_reason = finish_reason
        self.calls = 0

    async def chat(self, **_kwargs: Any) -> Dict[str, Any]:
        self.calls += 1
        return {
            "content": self.content,
            "provider_used": "fake-provider",
            "model": "fake-model",
            "finish_reason": self.finish_reason,
            "tokens": {"total_tokens": 10},
        }


class FailingChat:
    async def chat(self, **_kwargs: Any) -> Dict[str, Any]:
        raise RuntimeError("simulated timeout")


class SequenceChat:
    """按顺序返回正文或抛出异常，用于验证有界补试。"""

    def __init__(self, sequence: List[Any]) -> None:
        self.sequence = list(sequence)
        self.calls = 0

    async def chat(self, **_kwargs: Any) -> Dict[str, Any]:
        self.calls += 1
        item = self.sequence.pop(0)
        if isinstance(item, BaseException):
            raise item
        return {
            "content": item,
            "provider_used": "fake-provider",
            "model": "fake-model",
            "finish_reason": "stop",
            "tokens": {"total_tokens": 10},
        }


def _client(content: str, *, finish_reason: str = "stop", direct_only: bool = True) -> ExtractorClient:
    client = ExtractorClient(
        ExtractorConfig(
            enabled=True,
            direct_only=direct_only,
            direct_fallback=True,
            audit_provider="managed",
            main_provider="managed",
            audit_model="fake-model",
            main_model="fake-model",
        )
    )
    client._direct_ai_client = StaticChat(content, finish_reason)
    return client


def _valid_item(**overrides: Any) -> Dict[str, Any]:
    item = {
        "problem_type": "typo",
        "original": "决算市全口径",
        "quote": "决算市全口径",
        "context": "决算数大于预算数的主要原因：决算市全口径。",
        "suggestion": "“决算是全口径”请核对。",
        "severity": "low",
        "confidence": 0.9,
        "page": 1,
        "table_or_section": "支出说明",
        "check": "错别字",
    }
    item.update(overrides)
    return item


def _ai_item(**overrides: Any) -> IssueItem:
    service = AIFindingsService(AnalysisConfig(ai_enabled=True))
    item = service._build_ai_issue_item(
        raw_issue={
            "problem_type": "sum_mismatch",
            "original": "2026年项目支出合计100万元，分项60万元和30万元。",
            "quote": "2026年项目支出合计100万元，分项60万元和30万元。",
            "context": "2026年项目支出合计100万元，分项60万元和30万元。",
            "suggestion": "请将2026年合计修正为90万元。",
            "severity": "high",
            "confidence": 0.9,
            "page": 1,
            "expected": "90万元",
            "actual": "100万元",
            "difference": "10万元",
            "check": "合计勾稽",
            "table_or_section": "项目支出情况说明",
            **overrides,
        },
        context=JobContext(
            job_id="job-test",
            pdf_path="",
            page_texts=["2026年项目支出合计100万元，分项60万元和30万元。"],
        ),
        idx=0,
        page_number=1,
    )
    return item


def _service(content: str, *, finish_reason: str = "stop") -> AIFindingsService:
    service = AIFindingsService(AnalysisConfig(ai_enabled=True))
    service.ai_client.config.enabled = True
    service.ai_client.config.direct_only = True
    service.ai_client.config.direct_fallback = False
    service.ai_client._direct_ai_client = StaticChat(content, finish_reason)
    return service


# 1. 同年份不能掩盖真实金额差额 -------------------------------------------------
@pytest.mark.asyncio
async def test_same_year_does_not_mask_amount_difference() -> None:
    text = "2026年项目支出合计100万元，分项60万元和30万元。"
    payload = json.dumps(
        [
            {
                "problem_type": "sum_mismatch",
                "original": text,
                "quote": text,
                "context": text,
                "suggestion": "请将2026年合计修正为90万元。",
                "severity": "high",
                "confidence": 0.9,
                "page": 1,
                "expected": "90万元",
                "actual": "100万元",
                "difference": "10万元",
                "check": "合计勾稽",
                "table_or_section": "项目支出情况说明",
            }
        ],
        ensure_ascii=False,
    )
    client = _client(payload)
    result = await client._direct_semantic_audit(text, specialties=[TEXT_SEMANTICS])
    assert len(result) == 1
    assert result[0]["expected"] == "90万元"
    assert result[0]["span"] == [0, len(text)]


# 2. 元转万元必须按原始元数四舍五入 ---------------------------------------------
def test_yuan_to_wan_rounding_is_decimal_and_not_swallowed_by_tolerance() -> None:
    table_wan = normalize_amount_to_wan("123456", "元")
    state, expected = classify_display_conversion("12.34", 2, table_wan, 4)
    assert state == "mismatch"
    assert expected == Decimal("12.35")
    ok_state, _ = classify_display_conversion("12.35", 2, table_wan, 4)
    assert ok_state == "ok"


# 3. 合法显示舍入不新增硬错误 ---------------------------------------------------
def test_legal_display_rounding_is_hint_not_mismatch() -> None:
    level, diff = classify_amount_diff("1367.76", "1367.75", n_children=1)
    assert level == "rounding_hint"
    assert diff == Decimal("0.01")


# 4. expected 等解释字段贯通，且保持“模型未核实” --------------------------------
def test_expected_fields_survive_and_model_claim_is_marked() -> None:
    item = _ai_item()
    assert item.expected == "90万元"
    assert item.actual == "100万元"
    assert item.difference == "10万元"
    assert item.check == "合计勾稽"
    assert item.table_or_section == "项目支出情况说明"
    assert item.amounts_provenance == "model_claimed_not_verified"
    assert item.calculation_basis["verified"] is False
    dumped = item.model_dump()
    restored = IssueItem.model_validate(dumped)
    assert restored.expected == "90万元"
    assert restored.amounts_provenance == "model_claimed_not_verified"


def test_full_quote_is_not_truncated_for_evidence() -> None:
    quote = "甲" * 300 + "。"
    item = _ai_item(original=quote, quote=quote, context=quote, suggestion="核对")
    assert len(item.evidence[0]["text"]) > 200
    assert item.evidence[0]["text"] == quote


# 5. 未知/跨文档事实与证据 ID 不得获得合法关联 ----------------------------------
def test_unknown_or_mismatched_ids_are_not_linked() -> None:
    service = AIFindingsService(AnalysisConfig())
    context = JobContext(
        job_id="job-ids",
        pdf_path="",
        page_texts=["2026年项目支出合计100万元。"],
        fact_context={
            "facts": [
                {
                    "fact_uid": "fact-current",
                    "evidence_ids": ["ev-current"],
                }
            ]
        },
        meta={"check_receipts": [{"rule_id": "C-001", "sub_check_id": "total"}]},
    )
    item = service._build_ai_issue_item(
        raw_issue={
            "original": "2026年项目支出合计100万元。",
            "quote": "2026年项目支出合计100万元。",
            "context": "2026年项目支出合计100万元。",
            "severity": "high",
            "confidence": 0.9,
            "fact_ids": ["fact-other-document"],
            "evidence_ids": ["ev-other-document"],
            "check_relation_id": "C-999:unknown",
        },
        context=context,
        idx=0,
        page_number=1,
    )
    assert item.fact_ids == []
    assert item.evidence_ids == []
    assert item.check_relation_id is None
    assert item.metrics["ai_candidate_contract_state"] == "rejected"
    assert item.metrics["ai_merge_eligible"] is False


def test_known_but_mismatched_evidence_does_not_link() -> None:
    service = AIFindingsService(AnalysisConfig())
    context = JobContext(
        job_id="job-ids-2",
        pdf_path="",
        page_texts=["2026年项目支出合计100万元。"],
        fact_context={
            "facts": [
                {"fact_uid": "fact-current", "evidence_ids": ["ev-current"]},
                {"fact_uid": "fact-other", "evidence_ids": ["ev-other"]},
            ]
        },
        meta={"check_receipts": [{"rule_id": "C-001", "sub_check_id": "total"}]},
    )
    item = service._build_ai_issue_item(
        raw_issue={
            "original": "2026年项目支出合计100万元。",
            "quote": "2026年项目支出合计100万元。",
            "context": "2026年项目支出合计100万元。",
            "severity": "high",
            "confidence": 0.9,
            "fact_ids": ["fact-current"],
            "evidence_ids": ["ev-other"],
            "check_relation_id": "C-001:total",
        },
        context=context,
        idx=0,
        page_number=1,
    )
    assert item.metrics["ai_merge_eligible"] is False
    assert "fact_evidence_mismatch" in item.metrics["ai_candidate_rejection_reasons"]


# 6. 非法数组/混合非法条目/截断/超时不得伪装为成功无问题 -------------------------
@pytest.mark.asyncio
async def test_invalid_array_is_not_success() -> None:
    service = _service('[1,"bad",null]')
    issues = await service.analyze(
        JobContext(job_id="job-invalid", pdf_path="", page_texts=["正常原文。"])
    )
    assert issues == []
    assert any(item.get("type") == "audit_window_invalid" for item in service.ai_errors)
    assert service.ai_coverage["status"] == "partial"
    ledger = service.ai_client.pop_call_ledger()
    execution = build_ai_execution(True, call_ledger=ledger, window_errors=service.ai_errors)
    assert execution["state"] == AI_STATE_FAILED


@pytest.mark.asyncio
async def test_mixed_valid_and_invalid_keeps_valid_and_marks_partial() -> None:
    payload = json.dumps([_valid_item(), 1, {"problem_type": "typo"}], ensure_ascii=False)
    service = _service(payload)
    issues = await service.analyze(
        JobContext(job_id="job-partial", pdf_path="", page_texts=["决算市全口径。"])
    )
    assert len(issues) == 1
    assert issues[0].expected is None
    assert any(item.get("type") == "audit_window_partial" for item in service.ai_errors)
    assert service.ai_coverage["invalid_calls"] == 0
    assert service.ai_coverage["partial_calls"] >= 1


@pytest.mark.asyncio
async def test_truncated_response_is_not_success_and_does_not_fallback() -> None:
    client = _client(json.dumps([_valid_item()], ensure_ascii=False), finish_reason="length")
    client.semantic_service_outcome = AsyncMock(return_value=AuditOutcome(status="empty"))
    outcome = await client.audit_full_report_outcome(
        "决算市全口径。", "hash", specialties=[TEXT_SEMANTICS]
    )
    assert outcome.status == "truncated"
    assert outcome.accepted_count == 1
    assert client.semantic_service_outcome.await_count == 0
    execution = build_ai_execution(True, call_ledger=client.pop_call_ledger())
    assert execution["state"] == AI_STATE_FAILED
    assert execution["error_code"] == "ai_truncated_response"


@pytest.mark.asyncio
async def test_timeout_is_failed_not_empty_success() -> None:
    client = _client("[]")
    client._direct_ai_client = FailingChat()
    outcome = await client.audit_full_report_outcome("正常原文。", "hash")
    assert outcome.status == "error"
    execution = build_ai_execution(True, call_ledger=client.pop_call_ledger())
    assert execution["state"] == AI_STATE_FAILED
    assert execution["error_code"]


def _server_error(*, excerpt: str = "") -> LLMError:
    return LLMError(
        error_type=LLMErrorType.SERVER_ERROR,
        message="upstream 500",
        status_code=502,
        provider="managed",
        response_excerpt=excerpt,
    )


@pytest.mark.asyncio
async def test_unit_retry_recovers_once_without_duplicate_coverage() -> None:
    client = _client("[]")
    client.config.specialty_retries = 1
    client.config.specialty_retry_delay = 0
    chat = SequenceChat([_server_error(excerpt="upstream fail api_key=secret-123"), "[]"])
    client._direct_ai_client = chat

    outcome = await client.audit_full_report_outcome(
        "正常原文。", "hash", specialties=[TEXT_SEMANTICS]
    )

    assert chat.calls == 2
    assert outcome.status == "empty"
    assert outcome.attempts == 2
    assert outcome.retry_count == 1
    assert outcome.retried is True
    assert outcome.errors == []
    assert outcome.calls[0]["attempts"] == 2
    assert outcome.calls[0]["retry_count"] == 1
    # 失败响应体只进私有 trace，且凭据已脱敏；公开 dict 不带正文。
    excerpt = outcome.raw_exchanges[0]["error"]["response_excerpt"]
    assert "secret-123" not in excerpt
    assert "<redacted>" in excerpt
    assert "secret-123" not in json.dumps(outcome.to_dict(), ensure_ascii=False)
    ledger = client.pop_call_ledger()
    assert len(ledger) == 2
    execution = build_ai_execution(True, call_ledger=ledger)
    assert execution["state"] == AI_STATE_SUCCEEDED


@pytest.mark.asyncio
async def test_unit_retry_stops_at_one_and_keeps_failure_diagnostics() -> None:
    client = _client("[]")
    client.config.specialty_retries = 1
    client.config.specialty_retry_delay = 0
    chat = SequenceChat([_server_error(), _server_error()])
    client._direct_ai_client = chat

    outcome = await client.audit_full_report_outcome(
        "正常原文。", "hash", specialties=[TEXT_SEMANTICS]
    )

    assert chat.calls == 2
    assert outcome.status == "error"
    assert outcome.attempts == 2
    assert outcome.retry_count == 1
    assert len(outcome.errors) == 1
    assert len(outcome.errors[0]["retry_history"]) == 2
    assert outcome.errors[0]["error_type"] == "server_error"
    assert outcome.errors[0]["http_status"] == 502
    ledger = client.pop_call_ledger()
    assert len(ledger) == 2
    execution = build_ai_execution(True, call_ledger=ledger)
    assert execution["state"] == AI_STATE_FAILED


@pytest.mark.asyncio
async def test_non_retryable_auth_error_is_not_retried() -> None:
    client = _client("[]")
    client.config.specialty_retries = 1
    client.config.specialty_retry_delay = 0
    auth_error = LLMError(
        error_type=LLMErrorType.AUTHENTICATION,
        message="bad key",
        status_code=401,
        provider="managed",
    )
    chat = SequenceChat([auth_error, "[]"])
    client._direct_ai_client = chat

    outcome = await client.audit_full_report_outcome(
        "正常原文。", "hash", specialties=[TEXT_SEMANTICS]
    )

    assert chat.calls == 1
    assert outcome.status == "error"
    assert outcome.attempts == 1
    assert outcome.retry_count == 0
    assert outcome.retryable is False


@pytest.mark.asyncio
async def test_partial_success_retries_only_failed_specialty() -> None:
    client = _client("[]")
    client.config.specialty_retries = 1
    client.config.specialty_retry_delay = 0
    # text_semantics: 首败后成功；narrative_consistency: 直接成功。
    chat = SequenceChat([_server_error(), "[]", "[]"])
    client._direct_ai_client = chat

    outcome = await client.audit_full_report_outcome("正常原文。", "hash")

    assert chat.calls == 3
    assert outcome.attempts == 3
    assert outcome.retry_count == 1
    assert outcome.retried is True
    assert len(outcome.calls) == 2
    assert [call["attempts"] for call in outcome.calls] == [2, 1]
    assert all(call["status"] in {"ok", "empty"} for call in outcome.calls)


def test_coverage_counts_units_not_attempts_and_names_failed_pages() -> None:
    service = AIFindingsService(AnalysisConfig(ai_enabled=True))
    text = "正常原文。"
    window = TextWindow(window_id=0, start=0, end=len(text), text=text, pages=[7])
    outcome_dict = {
        "status": "partial",
        "calls": [
            {
                "specialty": TEXT_SEMANTICS,
                "status": "ok",
                "attempts": 1,
                "retry_count": 0,
                "accepted_count": 1,
            },
            {
                "specialty": NARRATIVE_CONSISTENCY,
                "status": "error",
                "attempts": 2,
                "retry_count": 1,
                "retryable": True,
                "error_type": "server_error",
                "http_status": 502,
            },
        ],
        "attempts": 3,
    }
    coverage = service._build_coverage(
        all_text=text,
        page_offsets=[(0, len(text), 7)],
        windows=[window],
        gaps=[],
        window_results=[{"window": window, "outcome": outcome_dict, "issues": []}],
    )
    assert coverage["planned_calls"] == 2
    assert coverage["succeeded_calls"] == 1
    assert coverage["failed_calls"] == 1
    assert coverage["completed_calls"] == 2
    assert coverage["completed_calls_includes_failed"] is True
    assert coverage["retried_calls"] == 1
    assert coverage["retry_attempts"] == 1
    assert coverage["attempts_total"] == 3
    assert coverage["call_budget"]["max_attempts_per_unit"] == 2
    assert coverage["failed_units"][0]["pages"] == [7]
    assert coverage["failed_units"][0]["specialty"] == NARRATIVE_CONSISTENCY
    assert coverage["status"] == "partial"


def test_degraded_gate_keeps_effective_results_and_uses_partial_wording() -> None:
    execution = build_ai_execution(
        True,
        call_ledger=[
            {
                "provider": "fake-provider",
                "model": "fake-model",
                "content": "[]",
                "contract_ok": True,
                "finish_reason": "stop",
            }
        ],
        window_errors=[{"type": "audit_window_partial"}],
        coverage={
            "status": "partial",
            "planned_calls": 4,
            "succeeded_calls": 3,
            "failed_calls": 1,
            "accepted_candidates": 3,
        },
    )
    assert execution["state"] == AI_STATE_DEGRADED
    assert execution["planned_calls"] == 4
    assert execution["succeeded_calls"] == 3
    assert execution["completed_calls_includes_failed"] is True
    reasons = quality_gate_ai_reasons(execution)
    assert reasons[0]["code"] == "ai_partial"
    assert "已保留 3 条有效候选" in reasons[0]["message"]


# 7. 合法空数组与执行失败能够区分 -------------------------------------------------
def test_empty_array_is_success_and_invalid_contract_is_failed() -> None:
    client = _client("[]")
    client.record_call(
        "fake-provider",
        "fake-model",
        finish_reason="stop",
        content="[]",
        contract_ok=True,
    )
    succeeded = build_ai_execution(True, call_ledger=client.pop_call_ledger())
    assert succeeded["state"] == AI_STATE_SUCCEEDED

    client.record_call(
        "fake-provider",
        "fake-model",
        finish_reason="stop",
        content='[1,"bad"]',
        contract_ok=False,
    )
    failed = build_ai_execution(True, call_ledger=client.pop_call_ledger())
    assert failed["state"] == AI_STATE_FAILED
    assert failed["error_code"] == "ai_response_invalid"


# 8. 完整句段不会因切窗被误报为残句 ---------------------------------------------
def test_window_boundary_fragment_is_rejected() -> None:
    service = AIFindingsService(AnalysisConfig(ai_enabled=True))
    window = TextWindow(
        window_id=0,
        start=0,
        end=15,
        text="会行政运行费用按实际情况支出。",
        pages=[21],
        boundary="hard_split",
        hard_split=True,
        prefix_context="9、……主要原因：管委",
    )
    assert service._is_boundary_fragment(
        {"problem_type": "fragment", "span": [0, 15]}, window
    )
    window.prefix_context = "9、……主要原因：管委会行政运行费用按实际情况支出。"
    # 窗口起点前是句末标点时，不能仅凭 fragment 类型自动丢弃（仍由模型/人工判断）。
    window.prefix_context = "上一句已经结束。"
    assert not service._is_boundary_fragment(
        {"problem_type": "fragment", "span": [0, 5]}, window
    )


def test_window_builder_prefers_sentence_boundaries() -> None:
    text = "第一句完整。" * 30
    windows = build_text_windows(
        text,
        [(0, len(text), 1)],
        window_chars=40,
        overlap_chars=5,
        max_windows=20,
    )
    assert windows
    assert all(
        window.hard_split or window.text.endswith(("。", "！", "？", "；", "\n"))
        for window in windows
        if window.end < len(text)
    )


# 9. 重叠窗口去重保留完整证据；不同位置不被吞掉 ---------------------------------
def test_overlap_dedupe_merges_same_location_and_preserves_origins() -> None:
    service = AIFindingsService(AnalysisConfig())
    base = {
        "problem_type": "typo",
        "quote": "决算市全口径",
        "original": "决算市全口径",
        "suggestion": "应为决算是全口径",
        "severity": "low",
        "confidence": 0.8,
        "evidence_pairs": [{"quote": "决算市全口径", "page": 29}],
    }
    one = {**base, "span": [10, 20], "audit_origin": {"window_id": 0, "pages": [29]}}
    two = {**base, "span": [12, 22], "audit_origin": {"window_id": 1, "pages": [29]}}
    merged = service._dedupe_semantic_issues([one, two])
    assert len(merged) == 1
    assert merged[0]["merged_count"] == 2
    assert len(merged[0]["audit_origins"]) == 2
    assert merged[0]["evidence_pairs"]


def test_overlap_dedupe_keeps_same_text_at_different_locations() -> None:
    service = AIFindingsService(AnalysisConfig())
    base = {
        "problem_type": "typo",
        "quote": "决算市全口径",
        "original": "决算市全口径",
        "severity": "low",
        "confidence": 0.8,
    }
    one = {**base, "span": [10, 20], "audit_origin": {"window_id": 0, "pages": [29]}}
    two = {**base, "span": [200, 210], "audit_origin": {"window_id": 2, "pages": [31]}}
    assert len(service._dedupe_semantic_issues([one, two])) == 2


# 10. 跨段矛盾保留双方证据；口径不一致不强行比较 --------------------------------
def test_cross_section_conflict_requires_two_evidence_sides() -> None:
    window = "16、决算是全口径。18、决算市全口径。"
    one_side = validate_audit_items(
        [
            {
                "problem_type": "cross_section_conflict",
                "original": "决算市全口径",
                "quote": "决算市全口径",
                "context": window,
                "suggestion": "请核对两处表述",
                "severity": "high",
                "confidence": 0.9,
                "evidence_pairs": [{"quote": "决算市全口径", "page": 1}],
            }
        ],
        window_text=window,
    )
    assert one_side.status == "ok"
    assert one_side.accepted[0]["severity"] == "manual_review"
    assert one_side.accepted[0]["semantic_uncertainties"]

    two_sides = validate_audit_items(
        [
            {
                "problem_type": "cross_section_conflict",
                "original": "决算市全口径",
                "quote": "决算市全口径",
                "context": window,
                "suggestion": "请核对两处表述",
                "severity": "high",
                "confidence": 0.9,
                "expected": "165.80 万元",
                "actual": "141.12 万元",
                "difference": "24.68 万元",
                "evidence_pairs": [
                    {"quote": "决算是全口径", "page": 1},
                    {"quote": "决算市全口径", "page": 1},
                ],
            }
        ],
        window_text=window,
    )
    assert two_sides.accepted[0]["severity"] == "high"
    assert len(two_sides.accepted[0]["evidence_pairs"]) == 2


def test_rejected_evidence_pair_is_retained_with_reason() -> None:
    result = validate_audit_items(
        [
            {
                "problem_type": "cross_section_conflict",
                "original": "支出决算为 141.12 万元",
                "quote": "支出决算为 141.12 万元",
                "context": "年初预算为 165.80 万元，支出决算为 141.12 万元。",
                "suggestion": "请核对表内与说明",
                "severity": "manual_review",
                "confidence": 0.9,
                "evidence_pairs": [
                    {
                        "quote": "年初预算为 165.80 万元，支出决算为 141.12 万元。",
                        "page": 20,
                    },
                    {
                        "quote": "机关事业单位基本养老保险缴费 165.80 165.80 0.00 支出",
                        "page": 13,
                        "table_or_section": "一般公共预算财政拨款支出决算表",
                    },
                ],
            }
        ],
        window_text="年初预算为 165.80 万元，支出决算为 141.12 万元。",
    )
    assert result.status == "ok"
    accepted = result.accepted[0]
    assert len(accepted["evidence_pairs"]) == 1
    assert accepted["evidence_pairs_rejected"][0]["page"] == 13
    assert accepted["evidence_pairs_rejected"][0]["reason"] == "quote_not_contiguous_in_window"


def test_evidence_pair_pages_are_mapped_from_window_text() -> None:
    service = AIFindingsService(AnalysisConfig())
    service.page_offsets = [(0, 10, 1), (11, 21, 2)]
    window = TextWindow(
        window_id=0,
        start=11,
        end=21,
        text="第二页说明文字。",
        pages=[2],
    )
    issue = {
        "evidence_pairs": [
            {"quote": "第二页说明文字", "page": None},
        ]
    }
    service._map_evidence_pair_pages(issue, window)
    assert issue["evidence_pairs"][0]["page"] == 2


# 11. 辅助定位收到真实文本和正确页码；无文本时标记不可用 ------------------------
@pytest.mark.asyncio
async def test_locator_uses_page_texts_and_corrects_wrong_page() -> None:
    locator_chat = StaticChat(
        json.dumps(
            [{"page": 99, "text": "第二页说明文字。", "score": 0.9}],
            ensure_ascii=False,
        )
    )
    locator = AILocator(client=locator_chat)
    finding = IssueItem(
        id="rule-1",
        source="rule",
        rule_id="V33-222",
        severity="high",
        title="表文不一致",
        message="表文不一致",
        evidence=[{"page": 2, "text": "原证据"}],
        location={},
        evidence_ids=["ev-1"],
    )
    context = JobContext(
        job_id="locator",
        pdf_path="",
        page_texts=["第一页内容。", "第二页说明文字。"],
    )
    enhanced = await locator.enhance_finding(context, finding)
    assert enhanced.page_number == 2
    assert enhanced.location["ai_locator"]["page_corrected"] is True
    assert len(enhanced.evidence) == 2
    assert enhanced.evidence[0]["text"] == "原证据"
    assert locator.last_outcome.status == "ok"


@pytest.mark.asyncio
async def test_locator_without_text_does_not_call_model() -> None:
    class ShouldNotCall:
        calls = 0

        async def chat(self, **_kwargs: Any) -> Dict[str, Any]:
            self.calls += 1
            return {"content": "[]"}

    chat = ShouldNotCall()
    locator = AILocator(client=chat)
    finding = IssueItem(
        id="rule-2",
        source="rule",
        rule_id="V33-222",
        severity="high",
        title="表文不一致",
        message="表文不一致",
        evidence=[{"text": "原证据"}],
        location={},
    )
    context = JobContext(job_id="locator-empty", pdf_path="", page_texts=[])
    enhanced = await locator.enhance_finding(context, finding)
    assert chat.calls == 0
    assert locator.last_outcome.status == "unavailable_no_text"
    assert enhanced.location["ai_locator"]["status"] == "unavailable_no_text"
    assert enhanced.evidence == finding.evidence


@pytest.mark.asyncio
async def test_specialty_failure_is_attributed_to_its_own_window() -> None:
    service = AIFindingsService(AnalysisConfig(ai_enabled=True))
    service.page_offsets = [(0, 20, 1), (21, 41, 2)]

    async def fake_audit(window_text: str, _doc_hash: str, structured_context=None):
        window_id = 0 if window_text.startswith("A") else 1
        return AuditIssueList(
            [],
            AuditOutcome(
                status="partial",
                errors=[
                    {
                        "specialty": "narrative_consistency",
                        "reason": "call_failed",
                        "error": "server_error",
                    }
                ],
                calls=[{"specialty": "narrative_consistency", "status": "error"}],
            ),
        )

    service.ai_client.ai_full_report_audit = fake_audit
    windows = [
        TextWindow(window_id=0, start=0, end=20, text="A" * 20, pages=[1]),
        TextWindow(window_id=1, start=21, end=41, text="B" * 20, pages=[2]),
    ]
    results = await service._run_window_audits(windows, "hash", 41)
    assert len(results) == 2
    failures = [
        item for item in service.ai_errors if item.get("type") == "audit_specialty_failed"
    ]
    assert {item.get("window_index") for item in failures} == {0, 1}
    assert all(item.get("specialty") == "narrative_consistency" for item in failures)


# 12. 超出窗口上限时覆盖与执行状态如实显示 --------------------------------------
@pytest.mark.asyncio
async def test_window_limit_records_uncovered_range_and_degrades_execution() -> None:
    service = _service("[]")
    service.audit_max_windows = 1
    service.audit_window_chars = 200
    long_text = "第一句完整。" * 100
    issues = await service.analyze(
        JobContext(job_id="job-limit", pdf_path="", page_texts=[long_text])
    )
    assert issues == []
    assert service.ai_coverage["status"] == "partial"
    assert service.ai_coverage["uncovered_ranges"]
    ledger = service.ai_client.pop_call_ledger()
    execution = build_ai_execution(
        True,
        call_ledger=ledger,
        window_errors=service.ai_errors,
        coverage=service.ai_coverage,
    )
    assert execution["state"] == AI_STATE_DEGRADED
    assert execution["uncovered_ranges"]


# 13. 历史结果与人工确认状态兼容 -------------------------------------------------
def test_historical_issue_without_new_fields_is_readable() -> None:
    old = {
        "id": "old-1",
        "source": "ai",
        "rule_id": "AI-SEM-001",
        "severity": "medium",
        "title": "旧结果",
        "message": "旧结果",
        "evidence": [{"text": "旧证据"}],
        "location": {"page": 2},
    }
    item = IssueItem.model_validate(old)
    assert item.expected is None
    assert item.actual is None
    assert item.difference is None
    assert item.check is None
    assert item.table_or_section is None
    assert item.amounts_provenance is None
    assert item.page_number == 2


def test_ai_only_candidate_is_not_auto_confirmed() -> None:
    item = _ai_item()
    payload = {
        "job_id": "job-review",
        "status": "done",
        "quality_status": "complete",
        "analysis_conclusion": "findings_detected",
        "doc_type": "final",
        "report_year": 2026,
        "report_kind": "final",
        "result": {
            "ai_findings": [item.model_dump()],
            "rule_findings": [],
            "merged": {
                "merge_policy_version": "fact-check-relation-v1",
                "merged_ids": [],
                "candidate_issue_ids": [item.id],
                "manual_review_issue_ids": [],
            },
        },
        "review_reasons": [],
    }
    report = build_review_report(payload)
    assert all(issue["id"] != item.id for issue in report["issues"])
    candidate_records = [
        entry for entry in report["manual_review"] if entry.get("id") == item.id
    ]
    assert len(candidate_records) == 1
    assert candidate_records[0]["record_type"] == "ai_candidate"


# 14. 提示词与清单职责 -----------------------------------------------------------
def test_specialty_prompts_are_distinct_and_state_boundaries() -> None:
    text_prompt = render_specialty_instructions(TEXT_SEMANTICS, 0.75)
    narrative_prompt = render_specialty_instructions(NARRATIVE_CONSISTENCY, 0.75)
    assert specialty_prompt_version(TEXT_SEMANTICS, 0.75) != specialty_prompt_version(
        NARRATIVE_CONSISTENCY, 0.75
    )
    assert "逐字连续" in text_prompt
    assert "evidence_pairs" in narrative_prompt
    assert "123456 元 → 12.35 万元" in text_prompt
    assert "PDF 物理页码" in text_prompt
    assert "不改变本任务" in text_prompt
    assert set(SPECIALTIES) == {TEXT_SEMANTICS, NARRATIVE_CONSISTENCY}


@pytest.mark.parametrize("kind", ["final", "budget"])
def test_text_checklist_does_not_claim_general_missing_char_coverage(kind: str) -> None:
    items = {item["id"]: item for item in load_checklist(kind)["items"]}
    assert items["D-03"]["status"] == "ai_assisted"
    assert items["D-03"]["decision"] == "ai_only"
    assert "AI" in items["D-03"]["note"] or "大模型" in items["D-03"]["note"]
