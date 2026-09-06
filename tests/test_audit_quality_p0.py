"""审查质量整改 P0 契约测试（fix/audit-quality-remediation-20260905）。

覆盖 docs/SYSTEM_ISSUES_HANDOFF_2026-09-05.md §3.5/§3.6 的修复：
1. AI 执行状态机：请求 AI 后只有 succeeded 可以通过质量门；
2. 分析请求契约：legacy/structured 不请求 AI，冲突参数 422；
3. 质量门新增原因码：missing_required_table / ambiguous_table_schema /
   rule_evidence_incomplete / rule_execution_error / report_type_mismatch /
   rules_not_executed；
4. 报告类型归一化：dept_final/unit_final → FINAL，未知类型禁止默认 BUDGET；
5. RuleOutcome：只有 fail 能生成 finding，其余状态进运行摘要。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest
from fastapi import HTTPException

from api import runtime
from api.main import _evaluate_quality_gate
from config.settings import get_settings
from src.engine import pipeline as engine_pipeline
from src.engine.rule_outcome import (
    RuleDeferred,
    RuleOutcome,
    summarize_rule_outcomes,
)
from src.services.ai_execution import (
    AI_STATE_DEGRADED,
    AI_STATE_FAILED,
    AI_STATE_NOT_REQUESTED,
    AI_STATE_NOT_RUN,
    AI_STATE_SUCCEEDED,
    ai_execution_is_successful,
    build_ai_execution,
    quality_gate_ai_reasons,
)
from src.services.ps_schema_sync import PSSharedSchemaSync

GOOD_PAGES = {
    "page_count": 3,
    "text_page_count": 3,
    "low_text_pages": [],
    "low_text_page_count": 0,
    "scanned_pages": [],
    "scanned_page_count": 0,
    "page_coverage": 1.0,
}

FULL_EXECUTED_SUMMARY = {
    "total_rules": 10,
    "executed": 10,
    "pass": 10,
    "fail": 0,
    "not_applicable": 0,
    "insufficient_data": 0,
    "parse_error": 0,
    "execution_error": 0,
    "unresolved_total": 0,
    "failed_rules": [],
    "unresolved_rules": [],
}


def _reason_codes(gate) -> List[str]:
    return [reason["code"] for reason in gate["review_reasons"]]


# ---------------------------------------------------------------------------
# 1. AI 执行状态机
# ---------------------------------------------------------------------------


def test_ai_execution_not_requested():
    record = build_ai_execution(False)
    assert record["state"] == AI_STATE_NOT_REQUESTED
    assert record["requested"] is False
    assert ai_execution_is_successful(record) is True


def test_ai_execution_succeeded_with_call_evidence():
    ledger = [
        {
            "provider": "zhipu",
            "model": "glm-4.5-flash",
            "prompt_version": "v1",
            "finish_reason": "stop",
            "token_usage": {"total_tokens": 1234},
            "content": '[{"problem_type":"ratio_recalc"}]',
            "error": None,
        }
    ]
    record = build_ai_execution(True, call_ledger=ledger)
    assert record["state"] == AI_STATE_SUCCEEDED
    assert record["provider"] == "zhipu"
    assert record["model"] == "glm-4.5-flash"
    assert record["token_usage"] == {"total_tokens": 1234}
    assert ai_execution_is_successful(record) is True


def test_ai_execution_truncated_response_is_failed():
    """finish_reason=length（token 截断）按失败处理，不得解释为未发现问题。"""
    ledger = [
        {
            "provider": "zhipu",
            "model": "glm-4.5-flash",
            "finish_reason": "length",
            "content": '{"partial"',
            "error": None,
        }
    ]
    record = build_ai_execution(True, call_ledger=ledger)
    assert record["state"] == AI_STATE_FAILED


def test_ai_execution_empty_content_is_failed():
    ledger = [
        {
            "provider": "zhipu",
            "model": "glm-4.5-flash",
            "finish_reason": "stop",
            "content": "",
            "error": None,
        }
    ]
    record = build_ai_execution(True, call_ledger=ledger)
    assert record["state"] == AI_STATE_FAILED


def test_ai_execution_window_limit_is_degraded():
    ledger = [
        {
            "provider": "zhipu",
            "model": "glm-4.5-flash",
            "finish_reason": "stop",
            "content": "[]",
            "error": None,
        }
    ]
    record = build_ai_execution(
        True,
        call_ledger=ledger,
        window_errors=[{"type": "audit_window_limit_reached"}],
    )
    assert record["state"] == AI_STATE_DEGRADED
    assert ai_execution_is_successful(record) is False


def test_ai_execution_all_calls_failed():
    ledger = [
        {
            "provider": "zhipu",
            "model": "glm-4.5-flash",
            "error": "timeout",
        }
    ]
    record = build_ai_execution(True, call_ledger=ledger)
    assert record["state"] == AI_STATE_FAILED
    assert record["error_code"] == "timeout"


def test_ai_execution_no_evidence_is_not_run():
    """没有任何调用留痕时禁止呈现为已执行——这是假完成的核心判据。"""
    record = build_ai_execution(True, ai_error="")
    assert record["state"] == AI_STATE_NOT_RUN
    assert ai_execution_is_successful(record) is False
    record_with_error = build_ai_execution(True, ai_error="AI分析超时(90s)")
    assert record_with_error["state"] == AI_STATE_FAILED
    assert record_with_error["error_code"] == "ai_timeout"


def test_ai_execution_fallback_counts_as_degraded():
    ledger = [
        {
            "provider": "zhipu",
            "model": "glm-4.5-flash",
            "finish_reason": "stop",
            "content": "[]",
            "error": None,
        }
    ]
    record = build_ai_execution(True, call_ledger=ledger, fallback="engine_only")
    assert record["state"] == AI_STATE_DEGRADED


def test_ai_gate_reasons_not_run_and_failed():
    not_run = {"requested": True, "state": AI_STATE_NOT_RUN, "error_code": "legacy_mode_no_ai"}
    assert [r["code"] for r in quality_gate_ai_reasons(not_run)] == ["ai_not_run"]
    failed = {"requested": True, "state": AI_STATE_FAILED, "error_code": "ai_timeout"}
    assert [r["code"] for r in quality_gate_ai_reasons(failed)] == ["ai_failed"]
    succeeded = {"requested": True, "state": AI_STATE_SUCCEEDED}
    assert quality_gate_ai_reasons(succeeded) == []
    assert quality_gate_ai_reasons({"requested": False, "state": AI_STATE_NOT_REQUESTED}) == []


# ---------------------------------------------------------------------------
# 1.1 真实 ledger 链路（2026-09-06 GPT5.6 复核整改）
#
# 缺陷背景：record_call 只落 content_length，ai_execution._call_succeeded
# 要求 content 非空——真实成功调用必被判 ai_empty_response，dual 模式
# 无法进入 succeeded。上方状态机测试手工构造带 content 的 ledger，
# 掩盖了生产接口不一致。以下测试走 record_call → pop_call_ledger →
# build_ai_execution 真实链路，锁定契约。
# ---------------------------------------------------------------------------


def _extractor_client():
    from src.engine.ai.extractor_client import ExtractorClient

    return ExtractorClient()


def test_real_ledger_chain_successful_call_is_succeeded():
    """真实 record_call 成功留痕（JSON 数组正文）→ 状态机必须判 succeeded。"""
    client = _extractor_client()
    client.record_call(
        "zhipu",
        "glm-4.5-flash",
        prompt_version="audit-v1",
        finish_reason="stop",
        token_usage={"total_tokens": 1234},
        content='[{"problem_type":"repeat_word"}]',
    )
    record = build_ai_execution(True, call_ledger=client.pop_call_ledger())
    assert record["state"] == AI_STATE_SUCCEEDED, record
    assert record["provider"] == "zhipu"
    assert record["model"] == "glm-4.5-flash"
    assert ai_execution_is_successful(record) is True


def test_real_ledger_chain_empty_json_array_is_succeeded():
    """模型诚实回答「未发现问题」（正文 '[]'）：非空字符串即真实调用证据。"""
    client = _extractor_client()
    client.record_call(
        "zhipu",
        "glm-4.5-flash",
        finish_reason="stop",
        content="[]",
    )
    record = build_ai_execution(True, call_ledger=client.pop_call_ledger())
    assert record["state"] == AI_STATE_SUCCEEDED, record


def test_real_ledger_chain_empty_content_is_failed():
    """真实 record_call 空正文（推理耗尽）→ 状态机必须判 failed/ai_empty_response。"""
    client = _extractor_client()
    client.record_call(
        "zhipu",
        "glm-4.5-flash",
        finish_reason="stop",
        content="",
    )
    record = build_ai_execution(True, call_ledger=client.pop_call_ledger())
    assert record["state"] == AI_STATE_FAILED
    assert record["error_code"] == "ai_empty_response"


def test_real_ledger_chain_truncated_is_failed():
    """真实 record_call 截断（finish_reason=length）→ failed/ai_truncated_response。"""
    client = _extractor_client()
    client.record_call(
        "zhipu",
        "glm-4.5-flash",
        finish_reason="length",
        content='[{"problem_type":',
    )
    record = build_ai_execution(True, call_ledger=client.pop_call_ledger())
    assert record["state"] == AI_STATE_FAILED
    assert record["error_code"] == "ai_truncated_response"


def test_real_ledger_chain_error_call_is_failed():
    """真实 record_call 异常留痕 → failed，error_code 取错误文本。"""
    client = _extractor_client()
    client.record_call(
        "zhipu",
        "glm-4.5-flash",
        error="timeout",
    )
    record = build_ai_execution(True, call_ledger=client.pop_call_ledger())
    assert record["state"] == AI_STATE_FAILED
    assert record["error_code"] == "timeout"


def test_real_ledger_chain_extractor_service_hits_counts_as_evidence():
    """抽取服务路径：hits 非空即以 json.dumps(hits) 作为 content 留痕。"""
    client = _extractor_client()
    client.record_call(
        "extractor_service",
        "remote-model",
        prompt_version="semantic_audit",
        token_usage={"total_tokens": 99},
        content='[{"hit":1}]',
    )
    record = build_ai_execution(True, call_ledger=client.pop_call_ledger())
    assert record["state"] == AI_STATE_SUCCEEDED, record
    assert record["provider"] == "extractor_service"


@pytest.mark.asyncio
async def test_legacy_pipeline_records_ai_not_run_for_stale_ai_flag(tmp_path, monkeypatch):
    """旧任务 status.json 残留 legacy + use_ai_assist=true：如实记 not_run 并转复核。"""

    from api import main as pipeline_mod
    from unittest.mock import AsyncMock

    job_dir = tmp_path / "job-legacy-ai"
    job_dir.mkdir()
    (job_dir / "source.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    runtime.write_json_file(
        job_dir / "status.json",
        {
            "status": "queued",
            "mode": "legacy",
            "use_local_rules": True,
            "use_ai_assist": True,  # 旧默认值残留
            "report_year": 2025,
            "report_kind": "budget",
        },
    )
    full_text = "一般公共预算财政拨款支出预算表" * 10

    class _FakePdf:
        pages = [object()]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(pipeline_mod.pdfplumber, "open", lambda _path: _FakePdf())
    monkeypatch.setattr(pipeline_mod, "_extract_visible_text_from_page", lambda _page: full_text)
    monkeypatch.setattr(pipeline_mod, "_extract_tables_from_page", lambda _page: [])
    monkeypatch.setattr(
        pipeline_mod,
        "run_rules_in_process",
        AsyncMock(
            return_value={
                "issues": {"all": [], "error": [], "warn": [], "info": []},
                "rule_execution_summary": dict(FULL_EXECUTED_SUMMARY),
            }
        ),
    )
    monkeypatch.setattr(pipeline_mod, "persist_analysis_job_snapshot", AsyncMock(return_value=True))
    monkeypatch.setattr(
        pipeline_mod,
        "run_structured_ingest",
        AsyncMock(return_value={"status": "skipped", "review_item_count": 0, "review_items": []}),
    )
    monkeypatch.setattr(pipeline_mod.settings, "is_dual_mode_enabled", lambda: False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    await pipeline_mod._run_pipeline_inner(job_dir)

    payload = runtime.read_json_file(job_dir / "status.json", default={})
    ai_execution = payload["result"]["meta"]["ai_execution"]
    assert ai_execution["requested"] is True
    assert ai_execution["state"] == AI_STATE_NOT_RUN
    assert ai_execution["error_code"] == "legacy_mode_no_ai"
    assert payload["status"] == "review_required"
    assert "ai_not_run" in [r["code"] for r in payload["review_reasons"]]


# ---------------------------------------------------------------------------
# 2. 分析请求契约
# ---------------------------------------------------------------------------


class _DummyQueue:
    def __init__(self) -> None:
        self.enqueued: list[str] = []

    async def enqueue(self, job_id: str) -> None:
        self.enqueued.append(job_id)


async def _dummy_runner(_job_dir):
    return None


def _prepare_job(tmp_path, name="job-contract"):
    job_dir = tmp_path / name
    job_dir.mkdir()
    (job_dir / "source.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    return job_dir


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body, detail_fragment",
    [
        ({"mode": "legacy", "use_ai_assist": True}, "conflicting analysis parameters"),
        ({"mode": "structured", "use_ai_assist": True}, "conflicting analysis parameters"),
        ({"mode": "legacy", "use_local_rules": False}, "use_local_rules"),
        ({"mode": "auto"}, "invalid mode"),
        ({"mode": "dual", "use_ai_assist": True, "_disable_dual": True}, "dual mode is disabled"),
    ],
)
async def test_start_analysis_rejects_conflicting_requests(
    tmp_path, monkeypatch, body, detail_fragment
):
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    monkeypatch.setattr(runtime, "_pipeline_runner", _dummy_runner)
    monkeypatch.setattr(runtime, "_job_queue", _DummyQueue())
    monkeypatch.setattr(runtime, "persist_analysis_job_snapshot", _fake_persist)
    if body.pop("_disable_dual", False):
        monkeypatch.setattr(
            runtime, "get_settings", lambda: _settings_with_dual(False)
        )
    else:
        monkeypatch.setattr(
            runtime, "get_settings", lambda: _settings_with_dual(True)
        )

    _prepare_job(tmp_path)
    with pytest.raises(HTTPException) as excinfo:
        await runtime.start_analysis("job-contract", body)
    assert excinfo.value.status_code == 422
    assert detail_fragment in str(excinfo.value.detail)


@pytest.mark.asyncio
async def test_start_analysis_legacy_defaults_to_no_ai(tmp_path, monkeypatch):
    """legacy 请求默认不请求 AI：status.json 的 use_ai_assist 必须为 False。"""
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    monkeypatch.setattr(runtime, "_pipeline_runner", _dummy_runner)
    monkeypatch.setattr(runtime, "_job_queue", _DummyQueue())
    monkeypatch.setattr(runtime, "persist_analysis_job_snapshot", _fake_persist)
    monkeypatch.setattr(runtime, "get_settings", lambda: _settings_with_dual(True))

    _prepare_job(tmp_path)
    await runtime.start_analysis("job-contract", {})
    status = runtime.read_json_file(tmp_path / "job-contract" / "status.json")
    assert status["mode"] == "legacy"
    assert status["use_ai_assist"] is False


@pytest.mark.asyncio
async def test_start_analysis_dual_defaults_to_ai_requested(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    monkeypatch.setattr(runtime, "_pipeline_runner", _dummy_runner)
    monkeypatch.setattr(runtime, "_job_queue", _DummyQueue())
    monkeypatch.setattr(runtime, "persist_analysis_job_snapshot", _fake_persist)
    monkeypatch.setattr(runtime, "get_settings", lambda: _settings_with_dual(True))

    _prepare_job(tmp_path)
    await runtime.start_analysis("job-contract", {"mode": "dual"})
    status = runtime.read_json_file(tmp_path / "job-contract" / "status.json")
    assert status["mode"] == "dual"
    assert status["use_ai_assist"] is True


# ---------------------------------------------------------------------------
# 2.1 布尔参数真值归一（2026-09-06 K3 复核整改）
#
# 缺陷背景：start_analysis 此前用 ``is True`` 身份判断，字符串 "true"/"1"
# 会绕过 legacy/structured 的 422 冲突拦截、被静默当作 False 持久化；
# dual 分支的 ``bool()`` 又会把 "false" 扭曲成 True。以下矩阵锁定
# normalize_request_flag 的契约：字符串真值与布尔等价、未知取值 422。
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, None),
        (True, True),
        (False, False),
        ("true", True),
        (" True ", True),
        ("TRUE", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("false", False),
        ("0", False),
        ("no", False),
        ("off", False),
    ],
)
def test_normalize_request_flag_truthy_matrix(raw, expected):
    assert runtime.normalize_request_flag(raw, "use_ai_assist") == expected


@pytest.mark.parametrize("raw", ["", "  ", "2", "enable", "y", "n", "null", 5, 1, 0, [], {}])
def test_normalize_request_flag_rejects_unknown_values(raw):
    with pytest.raises(HTTPException) as excinfo:
        runtime.normalize_request_flag(raw, "use_ai_assist")
    assert excinfo.value.status_code == 422
    assert "use_ai_assist" in str(excinfo.value.detail)


@pytest.mark.asyncio
@pytest.mark.parametrize("truthy", [True, "true", "1", "yes", "on"])
async def test_start_analysis_string_truthy_conflicts_are_rejected(
    tmp_path, monkeypatch, truthy
):
    """字符串 "true"/"1" 请求 AI 在 legacy/structured 下必须同样 422。"""
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    monkeypatch.setattr(runtime, "_pipeline_runner", _dummy_runner)
    monkeypatch.setattr(runtime, "_job_queue", _DummyQueue())
    monkeypatch.setattr(runtime, "persist_analysis_job_snapshot", _fake_persist)
    monkeypatch.setattr(runtime, "get_settings", lambda: _settings_with_dual(True))

    _prepare_job(tmp_path)
    for mode in ("legacy", "structured"):
        with pytest.raises(HTTPException) as excinfo:
            await runtime.start_analysis(
                "job-contract", {"mode": mode, "use_ai_assist": truthy}
            )
        assert excinfo.value.status_code == 422
        assert "conflicting analysis parameters" in str(excinfo.value.detail)


@pytest.mark.asyncio
@pytest.mark.parametrize("falsy", [False, "false", "0", "no", "off"])
async def test_start_analysis_string_falsy_persists_as_false(
    tmp_path, monkeypatch, falsy
):
    """dual 下字符串假值必须归一为 False 持久化，不能被 bool() 扭曲成 True。"""
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    monkeypatch.setattr(runtime, "_pipeline_runner", _dummy_runner)
    monkeypatch.setattr(runtime, "_job_queue", _DummyQueue())
    monkeypatch.setattr(runtime, "persist_analysis_job_snapshot", _fake_persist)
    monkeypatch.setattr(runtime, "get_settings", lambda: _settings_with_dual(True))

    _prepare_job(tmp_path)
    await runtime.start_analysis(
        "job-contract", {"mode": "dual", "use_ai_assist": falsy}
    )
    status = runtime.read_json_file(tmp_path / "job-contract" / "status.json")
    assert status["use_ai_assist"] is False
    assert isinstance(status["use_ai_assist"], bool)


@pytest.mark.asyncio
@pytest.mark.parametrize("truthy", [True, "true", "1"])
async def test_start_analysis_dual_string_truthy_persists_as_true(
    tmp_path, monkeypatch, truthy
):
    """dual 下字符串真值必须归一为 True 持久化（此前 "true" 会被静默当 False）。"""
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    monkeypatch.setattr(runtime, "_pipeline_runner", _dummy_runner)
    monkeypatch.setattr(runtime, "_job_queue", _DummyQueue())
    monkeypatch.setattr(runtime, "persist_analysis_job_snapshot", _fake_persist)
    monkeypatch.setattr(runtime, "get_settings", lambda: _settings_with_dual(True))

    _prepare_job(tmp_path)
    await runtime.start_analysis(
        "job-contract", {"mode": "dual", "use_ai_assist": truthy}
    )
    status = runtime.read_json_file(tmp_path / "job-contract" / "status.json")
    assert status["use_ai_assist"] is True
    assert isinstance(status["use_ai_assist"], bool)


@pytest.mark.asyncio
@pytest.mark.parametrize("falsy", [False, "false", "0"])
async def test_start_analysis_legacy_string_falsy_local_rules_conflict(
    tmp_path, monkeypatch, falsy
):
    """legacy 下字符串 "false" 关停本地规则必须 422，不能静默放行。"""
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    monkeypatch.setattr(runtime, "_pipeline_runner", _dummy_runner)
    monkeypatch.setattr(runtime, "_job_queue", _DummyQueue())
    monkeypatch.setattr(runtime, "persist_analysis_job_snapshot", _fake_persist)
    monkeypatch.setattr(runtime, "get_settings", lambda: _settings_with_dual(True))

    _prepare_job(tmp_path)
    with pytest.raises(HTTPException) as excinfo:
        await runtime.start_analysis(
            "job-contract", {"mode": "legacy", "use_local_rules": falsy}
        )
    assert excinfo.value.status_code == 422
    assert "use_local_rules" in str(excinfo.value.detail)


class _SettingsStub:
    def __init__(self, dual_enabled: bool):
        self._dual_enabled = dual_enabled

    def is_dual_mode_enabled(self) -> bool:
        return self._dual_enabled


def _settings_with_dual(enabled: bool) -> _SettingsStub:
    return _SettingsStub(enabled)


async def _fake_persist(_payload, include_results: bool = False):
    return None


# ---------------------------------------------------------------------------
# 3. 质量门新增原因码
# ---------------------------------------------------------------------------


def _base_gate_kwargs(**overrides):
    kwargs = dict(
        page_assessment=GOOD_PAGES,
        report_kind="final",
        report_year=2025,
        ai_requested=False,
        ai_degraded=False,
        issue_total=0,
        evidence_degraded_count=0,
    )
    kwargs.update(overrides)
    return kwargs


def test_gate_missing_required_table_reason():
    gate = _evaluate_quality_gate(
        **_base_gate_kwargs(
            structured_ingest={
                "review_items": [
                    {
                        "id": "FIN_05_general_public_expenditure:missing",
                        "type": "missing_core_table",
                        "table_code": "FIN_05_general_public_expenditure",
                        "severity": "warn",
                    }
                ]
            }
        )
    )
    assert gate["status"] == "review_required"
    assert "missing_required_table" in _reason_codes(gate)


def test_gate_ambiguous_table_schema_reason():
    gate = _evaluate_quality_gate(
        **_base_gate_kwargs(
            structured_ingest={
                "review_items": [
                    {"id": "TBL_X:unknown", "type": "unknown_table", "table_code": "TBL_X"},
                    {"id": "TBL_Y:low_confidence", "type": "low_confidence_table"},
                ]
            }
        )
    )
    assert "ambiguous_table_schema" in _reason_codes(gate)


def test_gate_rule_evidence_incomplete_reason():
    gate = _evaluate_quality_gate(
        **_base_gate_kwargs(evidence_completeness={"incomplete": 12})
    )
    assert "rule_evidence_incomplete" in _reason_codes(gate)
    assert gate["status"] == "review_required"


def test_gate_rule_execution_error_reason():
    summary = dict(FULL_EXECUTED_SUMMARY)
    summary.update(
        {
            "executed": 8,
            "pass": 8,
            "execution_error": 1,
            "parse_error": 1,
            "unresolved_total": 2,
            "unresolved_rules": [
                {"rule_id": "V33-115", "status": "parse_error"},
                {"rule_id": "V33-120", "status": "execution_error"},
            ],
        }
    )
    gate = _evaluate_quality_gate(
        **_base_gate_kwargs(issue_total=3, rule_execution_summary=summary)
    )
    assert "rule_execution_error" in _reason_codes(gate)


def test_gate_no_findings_blocked_when_rules_not_executed():
    summary = dict(FULL_EXECUTED_SUMMARY)
    summary.update(
        {
            "executed": 6,
            "pass": 6,
            "insufficient_data": 4,
            "unresolved_total": 4,
        }
    )
    gate = _evaluate_quality_gate(
        **_base_gate_kwargs(rule_execution_summary=summary)
    )
    assert gate["status"] == "review_required"
    assert "rules_not_executed" in _reason_codes(gate)
    assert gate["analysis_conclusion"] == "incomplete"


# ---------------------------------------------------------------------------
# 质量门三缺口（2026-09-06 GPT5.6 P0-2）
#
# 1. insufficient_data 不触发人工复核（此前只有 parse/execution_error 进门禁）；
# 2. rule_execution_summary=None 仍可能生成 no_findings（此前摘要缺失时
#    no_findings 门禁整体跳过）；
# 3. fact_materialization_empty 只把 parser_quality 标 poor，不阻断完成。
# ---------------------------------------------------------------------------


def test_gate_insufficient_data_gets_own_reason():
    """insufficient_data>0 必须触发 rules_insufficient_data 并转人工复核。"""
    summary = dict(FULL_EXECUTED_SUMMARY)
    summary.update(
        {
            "executed": 9,
            "pass": 9,
            "insufficient_data": 1,
            "unresolved_total": 1,
            "unresolved_rules": [
                {
                    "rule_id": "V33-115",
                    "status": "insufficient_data",
                    "detail": "表缺失或无可解析行: 收入支出决算总表",
                },
            ],
        }
    )
    gate = _evaluate_quality_gate(
        **_base_gate_kwargs(issue_total=2, rule_execution_summary=summary)
    )
    assert "rules_insufficient_data" in _reason_codes(gate)
    assert gate["status"] == "review_required"


def test_gate_no_findings_blocked_when_summary_missing():
    """无发现且规则执行摘要缺失：无法证明规则执行过，禁止 no_findings。"""
    gate = _evaluate_quality_gate(
        **_base_gate_kwargs(rule_execution_summary=None)
    )
    assert "rules_not_executed" in _reason_codes(gate)
    assert gate["status"] == "review_required"
    assert gate["analysis_conclusion"] == "incomplete"

    gate_empty = _evaluate_quality_gate(
        **_base_gate_kwargs(rule_execution_summary={})
    )
    assert "rules_not_executed" in _reason_codes(gate_empty)
    assert gate_empty["analysis_conclusion"] == "incomplete"


def test_gate_fact_materialization_empty_blocks_done():
    """已识别表格但 facts 全空：parser_quality=poor 之外必须转人工复核。"""
    gate = _evaluate_quality_gate(
        **_base_gate_kwargs(
            structured_ingest={
                "review_items": [
                    {
                        "id": "facts:none",
                        "type": "fact_materialization_empty",
                        "severity": "error",
                    }
                ]
            }
        )
    )
    assert "fact_materialization_empty" in _reason_codes(gate)
    assert gate["status"] == "review_required"
    assert gate["analysis_conclusion"] == "incomplete"
    gate = _evaluate_quality_gate(
        **_base_gate_kwargs(rule_execution_summary=dict(FULL_EXECUTED_SUMMARY))
    )
    assert gate["status"] == "done"
    assert gate["analysis_conclusion"] == "no_findings"


def test_gate_report_type_mismatch_doc_type_vs_content():
    gate = _evaluate_quality_gate(**_base_gate_kwargs(doc_type="dept_final", report_kind="budget"))
    assert "report_type_mismatch" in _reason_codes(gate)


def test_gate_report_type_mismatch_ps_sync_budget_misclass():
    """决算材料被写库为 BUDGET：必须被发现（样张实测缺陷）。"""
    gate = _evaluate_quality_gate(
        **_base_gate_kwargs(
            doc_type="dept_final",
            report_kind="final",
            structured_ingest={
                "ps_sync": {"status": "done", "report_type": "BUDGET"},
                "review_items": [],
            },
        )
    )
    assert "report_type_mismatch" in _reason_codes(gate)


def test_gate_report_type_mismatch_ps_sync_skipped():
    gate = _evaluate_quality_gate(
        **_base_gate_kwargs(
            doc_type="dept_final",
            report_kind="final",
            structured_ingest={
                "ps_sync": {"status": "skipped", "reason": "unknown_report_type"},
                "review_items": [],
            },
        )
    )
    assert "report_type_mismatch" in _reason_codes(gate)


def test_gate_ai_requested_but_not_run_forces_review():
    gate = _evaluate_quality_gate(
        **_base_gate_kwargs(
            ai_requested=True,
            ai_execution={"requested": True, "state": AI_STATE_NOT_RUN, "error_code": "legacy_mode_no_ai"},
        )
    )
    assert gate["status"] == "review_required"
    assert "ai_not_run" in _reason_codes(gate)


def test_gate_ai_succeeded_passes():
    gate = _evaluate_quality_gate(
        **_base_gate_kwargs(
            ai_requested=True,
            ai_execution={
                "requested": True,
                "state": AI_STATE_SUCCEEDED,
                "provider": "zhipu",
                "model": "glm-4.5-flash",
            },
            issue_total=2,
        )
    )
    assert gate["status"] == "done"
    assert gate["review_reasons"] == []


# ---------------------------------------------------------------------------
# 4. 报告类型归一化
# ---------------------------------------------------------------------------


def test_normalize_report_type_maps_doc_type_values():
    sync = PSSharedSchemaSync(None)  # type: ignore[arg-type]

    assert sync._normalize_report_type("budget") == "BUDGET"
    assert sync._normalize_report_type("预算") == "BUDGET"
    assert sync._normalize_report_type("dept_budget") == "BUDGET"
    assert sync._normalize_report_type("unit_budget") == "BUDGET"
    assert sync._normalize_report_type("final") == "FINAL"
    assert sync._normalize_report_type("决算") == "FINAL"
    assert sync._normalize_report_type("dept_final") == "FINAL"
    assert sync._normalize_report_type("unit_final") == "FINAL"
    # 未知类型禁止默认写成 BUDGET（样张 dept_final 被错分 BUDGET 的教训）
    assert sync._normalize_report_type("unknown") is None
    assert sync._normalize_report_type("") is None
    assert sync._normalize_report_type("something_else") is None


# ---------------------------------------------------------------------------
# 5. RuleOutcome
# ---------------------------------------------------------------------------


def test_summarize_rule_outcomes_counts_all_statuses():
    outcomes = [
        RuleOutcome(rule_id="V33-001", status="fail"),
        RuleOutcome(rule_id="V33-001", status="fail"),
        RuleOutcome(rule_id="CMM-001", status="pass"),
        RuleOutcome(rule_id="CMM-004", status="not_applicable", detail="域不同不可比"),
        RuleOutcome(rule_id="V33-115", status="insufficient_data", detail="列归属不明"),
        RuleOutcome(rule_id="V33-120", status="parse_error"),
        RuleOutcome(rule_id="V33-202", status="execution_error"),
    ]
    summary = summarize_rule_outcomes(outcomes)
    assert summary["total_rules"] == 7
    assert summary["fail"] == 2
    assert summary["pass"] == 1
    assert summary["not_applicable"] == 1
    assert summary["insufficient_data"] == 1
    assert summary["parse_error"] == 1
    assert summary["execution_error"] == 1
    assert summary["executed"] == 4
    assert summary["unresolved_total"] == 3
    assert summary["failed_rules"] == ["V33-001"]


class _DeferredRule:
    code, severity = "V33-TEST-DEFERRED", "warn"

    def apply(self, doc):
        raise RuleDeferred(self.code, "无法可靠确定列归属")


class _BoomRule:
    code, severity = "V33-TEST-BOOM", "warn"

    def apply(self, doc):
        raise ValueError("boom")


class _HitRule:
    code, severity = "V33-TEST-HIT", "warn"

    def apply(self, doc):
        from src.engine.rules_v33 import Issue

        return [Issue(rule=self.code, severity="warn", message="hit", location={"page": 1})]


class _QuietRule:
    code, severity = "V33-TEST-QUIET", "warn"

    def apply(self, doc):
        return []


def test_run_rules_with_outcomes_only_fail_produces_findings(monkeypatch):
    """insufficient_data / execution_error 不得产出 finding，只进运行摘要。"""

    monkeypatch.setattr(
        engine_pipeline, "ALL_COMMON_RULES", [_DeferredRule(), _BoomRule(), _HitRule(), _QuietRule()]
    )
    monkeypatch.setattr(engine_pipeline, "ALL_BUDGET_RULES", [])
    monkeypatch.setattr(engine_pipeline, "FINAL_ALL_RULES", [])

    class _Doc:
        pages = 1
        path = "x.pdf"
        page_texts = ["决算"]
        years_per_page: list = []
        units_per_page: list = []
        anchors: dict = {}

        def __init__(self):
            self.page_tables = [[]]

    issues, outcomes = engine_pipeline.run_rules_with_outcomes(_Doc(), False, report_kind="final")
    rule_ids = {issue.rule for issue in issues}
    assert "V33-TEST-DEFERRED" not in rule_ids
    assert "V33-TEST-BOOM" not in rule_ids
    assert "V33-TEST-HIT" in rule_ids

    summary = summarize_rule_outcomes(outcomes)
    assert summary["insufficient_data"] == 1
    assert summary["execution_error"] == 1
    assert summary["fail"] == 1
    assert summary["pass"] == 1
