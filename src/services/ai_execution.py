"""AI 执行状态机与质量门信号（P0 假完成修复）。

修复 docs/SYSTEM_ISSUES_HANDOFF_2026-09-05.md §3.5 的核心缺陷：
``use_ai_assist=true`` 与实际行为不符，结果里没有任何"AI 未运行"的显式状态，
使用者无法区分「AI 查过没发现问题」「AI 没查」「AI 失败降级」。

设计原则（fail-closed）：
- ``result.meta.ai_execution`` 如实记录 AI 是否被请求、是否真的执行成功；
- 没有调用留痕（provider/model/token/finish_reason）时**禁止**呈现为已执行；
- 请求 AI 后只有 ``succeeded`` 可以通过质量门；``not_run``、超时、空响应、
  token 截断和全部 provider 失败均转 ``review_required``。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

# AI 执行状态（五态）
AI_STATE_NOT_REQUESTED = "not_requested"  # 请求未要求 AI（legacy 纯规则模式 / dual+use_ai_assist=false）
AI_STATE_NOT_RUN = "not_run"             # 请求了 AI 但没有任何调用发生（配置禁用/旧任务 legacy 路径）
AI_STATE_SUCCEEDED = "succeeded"         # 有真实调用留痕且未触发失败判据
AI_STATE_DEGRADED = "degraded"           # 调用成功但覆盖不完整（如审计窗口上限触发）
AI_STATE_FAILED = "failed"               # 超时/空响应/截断/全部 provider 失败

AI_EXECUTION_VERSION = 1

# 空响应、finish_reason=length（token 截断）均按失败处理，不能解释为"未发现问题"
_TRUNCATED_FINISH_REASONS = {"length", "max_tokens"}
# 推理耗尽（思考 token 吃满预算导致正文为空）同样按失败处理
_EMPTY_CONTENT_MIN_CHARS = 0


def build_ai_execution(
    requested: bool,
    *,
    ai_error: str = "",
    fallback: Any = None,
    call_ledger: Optional[Iterable[Dict[str, Any]]] = None,
    provider_stats: Optional[Iterable[Dict[str, Any]]] = None,
    window_errors: Optional[Iterable[Dict[str, Any]]] = None,
    error_code: str = "",
) -> Dict[str, Any]:
    """从调用留痕推导 AI 执行状态，返回 ``result.meta.ai_execution`` 结构。

    判定优先级（证据优先于声明）：
    1. 未请求 → not_requested；
    2. 有任何一次成功调用留痕（provider/model 齐全且正文非空、未截断）：
       - 存在覆盖缺口（window_errors / fallback）→ degraded；
       - 否则 → succeeded；
    3. 有调用但全部失败（或正文空/截断）→ failed；
    4. 无任何调用留痕：
       - 有 ai_error → failed（错误说明尝试过但失败且未留下成功调用）；
       - 无 ai_error → not_run（无法证明执行，禁止呈现为已完成）。
    """

    ledger = [dict(item) for item in (call_ledger or []) if isinstance(item, dict)]
    stats = [dict(item) for item in (provider_stats or []) if isinstance(item, dict)]
    windows = [dict(item) for item in (window_errors or []) if isinstance(item, dict)]

    record: Dict[str, Any] = {
        "version": AI_EXECUTION_VERSION,
        "requested": bool(requested),
        "state": AI_STATE_NOT_REQUESTED,
        "attempts": len(ledger),
        "provider": None,
        "model": None,
        "prompt_version": None,
        "finish_reason": None,
        "token_usage": None,
        "error_code": error_code or None,
    }

    if not requested:
        return record

    successful = [item for item in ledger if _call_succeeded(item)]
    if successful:
        last = successful[-1]
        record["provider"] = last.get("provider")
        record["model"] = last.get("model")
        record["prompt_version"] = last.get("prompt_version")
        record["finish_reason"] = last.get("finish_reason")
        record["token_usage"] = last.get("token_usage")
        if windows or fallback:
            record["state"] = AI_STATE_DEGRADED
            record["error_code"] = error_code or (
                "audit_window_limit_reached"
                if windows
                else "fallback_triggered"
            )
        else:
            record["state"] = AI_STATE_SUCCEEDED
        return record

    # 没有成功调用：区分"有响应但不可用"、"调用失败"、"完全无留痕"
    failed_call = next((item for item in reversed(ledger) if item.get("error")), None)
    if failed_call is not None:
        record["state"] = AI_STATE_FAILED
        record["error_code"] = error_code or str(failed_call.get("error") or "ai_call_failed")
        record["provider"] = failed_call.get("provider")
        record["model"] = failed_call.get("model")
        record["finish_reason"] = failed_call.get("finish_reason")
        return record

    # 拿到了响应但不可用：finish_reason=length（token 截断）或空正文。
    # 这些都按失败处理，不能解释为"AI 审计后未发现问题"。
    unusable = [item for item in ledger if not item.get("error")]
    if unusable:
        last = unusable[-1]
        finish_reason = str(last.get("finish_reason") or "").strip().lower()
        if finish_reason in _TRUNCATED_FINISH_REASONS:
            record["state"] = AI_STATE_FAILED
            record["error_code"] = error_code or "ai_truncated_response"
            record["provider"] = last.get("provider")
            record["model"] = last.get("model")
            record["finish_reason"] = last.get("finish_reason")
            return record
        if not str(last.get("content") or "").strip():
            record["state"] = AI_STATE_FAILED
            record["error_code"] = error_code or "ai_empty_response"
            record["provider"] = last.get("provider")
            record["model"] = last.get("model")
            record["finish_reason"] = last.get("finish_reason")
            return record

    if stats:
        # 有 provider_stats（可能是失败留痕）但没有结构化 ledger：如实判 failed
        error_stat = next(
            (item for item in reversed(stats) if item.get("error")), None
        )
        record["state"] = AI_STATE_FAILED
        record["error_code"] = error_code or str(
            (error_stat or {}).get("error") or "ai_call_failed"
        )
        record["provider"] = (error_stat or {}).get("provider_used")
        record["model"] = (error_stat or {}).get("model_used")
        return record

    if ai_error:
        record["state"] = AI_STATE_FAILED
        record["error_code"] = error_code or _short_error_code(ai_error)
        return record

    record["state"] = AI_STATE_NOT_RUN
    record["error_code"] = error_code or "no_call_evidence"
    return record


def _call_succeeded(item: Dict[str, Any]) -> bool:
    """单次调用留痕是否构成"成功证据"。

    空正文、finish_reason=length（token 截断）或显式 error 均不视为成功。
    """

    if item.get("error"):
        return False
    provider = str(item.get("provider") or "").strip()
    model = str(item.get("model") or "").strip()
    content = str(item.get("content") or "")
    if not provider or not model:
        return False
    if len(content.strip()) <= _EMPTY_CONTENT_MIN_CHARS:
        return False
    finish_reason = str(item.get("finish_reason") or "").strip().lower()
    if finish_reason in _TRUNCATED_FINISH_REASONS:
        return False
    return True


def _short_error_code(ai_error: str) -> str:
    """把错误文本压缩成稳定的 error_code（不含 PDF 原文，避免敏感信息外带）。"""

    text = str(ai_error or "").strip()
    if not text:
        return "ai_error"
    lowered = text.lower()
    if "timeout" in lowered or "超时" in text:
        return "ai_timeout"
    if "no providers" in lowered or "all providers" in lowered:
        return "ai_provider_unavailable"
    return "ai_error:" + text[:80]


def ai_execution_is_successful(ai_execution: Optional[Dict[str, Any]]) -> bool:
    """质量门判据：请求 AI 后只有 succeeded 算通过。"""

    if not isinstance(ai_execution, dict):
        return False
    if not ai_execution.get("requested"):
        return True
    return str(ai_execution.get("state") or "") == AI_STATE_SUCCEEDED


def quality_gate_ai_reasons(ai_execution: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """根据 ai_execution 生成质量门 review_reasons（可能为空）。

    - ``ai_not_run``：请求了但没有任何调用留痕；
    - ``ai_failed``：请求了但失败/降级（含截断、空响应、超时、provider 回退）。
    """

    if not isinstance(ai_execution, dict) or not ai_execution.get("requested"):
        return []
    state = str(ai_execution.get("state") or "")
    if state == AI_STATE_SUCCEEDED:
        return []
    error_code = str(ai_execution.get("error_code") or "")
    if state in (AI_STATE_NOT_REQUESTED, AI_STATE_NOT_RUN):
        return [
            {
                "code": "ai_not_run",
                "message": (
                    "请求了 AI 辅助但本次没有任何 AI 调用留痕，"
                    "结论覆盖面缺少 AI 审计部分，需人工复核或重新分析"
                ),
                "ai_state": state,
                "error_code": error_code,
            }
        ]
    return [
        {
            "code": "ai_failed",
                "message": (
                    "请求了 AI 辅助但执行未成功（超时、空响应、token 截断或全部 "
                    "provider 失败均按失败处理），不能呈现为「AI 已审计且未发现问题」"
                ),
            "ai_state": state,
            "error_code": error_code,
        }
    ]
