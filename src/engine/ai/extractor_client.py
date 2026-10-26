#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI抽取器客户端 - 后端调用AI抽取器微服务的客户端
"""

import os
import asyncio
import logging
import time
from typing import List, Dict, Any, Optional
import hashlib
import json
import re
import httpx
from dataclasses import dataclass, field

from src.services.ai_client import AIClient
from src.providers import LLMError
from src.providers.base import sanitize_error_excerpt
from src.engine.ai.audit_contract import validate_audit_items
from src.engine.ai.audit_prompts import (
    NARRATIVE_CONSISTENCY,
    SPECIALTIES,
    TEXT_SEMANTICS,
    render_specialty_instructions,
    specialty_prompt_version,
    specialty_prompt_versions,
)
from src.utils.logging_config import describe_exception, fingerprint_for_log
from src.utils.provenance import (
    build_finding_provenance,
    build_model_version,
    prompt_version_from_template,
)

logger = logging.getLogger(__name__)

# ==================== 配置 ====================
DEFAULT_EXTRACTOR_URL = "http://127.0.0.1:9009/ai/extract/v1"

# 超时和重试配置
REQUEST_TIMEOUT = 120.0  # 增加到120秒，适应复杂文档处理
MAX_RETRIES = 2
RETRY_DELAY = 1.0


def _read_float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def _read_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _read_str_env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _read_int_env(name: str, default: int, minimum: int = 0, maximum: int = 10) -> int:
    try:
        value = int(str(os.getenv(name, str(default))).strip())
    except Exception:
        value = default
    return max(minimum, min(maximum, value))


MIN_ISSUE_CONFIDENCE = _read_float_env("AI_MIN_ISSUE_CONFIDENCE", 0.75)

# ==================== 提示词与响应契约 ====================
# 直连审计按两个专项分别调用：文字语义、说明合理性/跨段一致性。
# 旧的单块提示词保留版本号兼容接口，但实际调用不再使用它。
FULL_REPORT_AUDIT_PROMPT_ID = "full_report_audit_direct"


def render_full_report_audit_instructions() -> str:
    """兼容接口：返回两个专项提示词的合并文本（仅用于版本对照）。"""
    return "\n\n".join(
        render_specialty_instructions(specialty, MIN_ISSUE_CONFIDENCE)
        for specialty in SPECIALTIES
    )


def full_report_audit_prompt_version() -> str:
    """兼容接口：两个专项提示词合并后的版本号。"""
    return prompt_version_from_template(
        FULL_REPORT_AUDIT_PROMPT_ID,
        render_full_report_audit_instructions(),
    )


#: 抽取服务侧语义审计的任务契约标识。提示词实际由远端服务持有，
#: 本地只能如实记录调用的任务契约版本，不假装知道远端提示词内容。
SEMANTIC_AUDIT_SERVICE_TASK = "semantic_audit_v1"


@dataclass
class AuditOutcome:
    """一次（或一组专项）审计调用的可追溯结果。"""

    issues: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "not_run"
    provider: Optional[str] = None
    model: Optional[str] = None
    prompt_version: Optional[str] = None
    prompt_versions: List[str] = field(default_factory=list)
    finish_reason: Optional[str] = None
    token_usage: Optional[Dict[str, Any]] = None
    input_chars: int = 0
    raw_count: int = 0
    accepted_count: int = 0
    rejected_count: int = 0
    rejection_reasons: Dict[str, int] = field(default_factory=dict)
    response_sha256: Optional[str] = None
    response_chars: int = 0
    fallback_used: bool = False
    fallback_reason: Optional[str] = None
    specialty: Optional[str] = None
    calls: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[Dict[str, Any]] = field(default_factory=list)
    #: 同一检查单元（专项×窗口）的尝试次数与逐次结果；覆盖计数只按单元算，
    #: 补试次数单独统计，不进入 planned 分母。
    attempts: int = 1
    retry_count: int = 0
    retried: bool = False
    retryable: bool = False
    error_type: Optional[str] = None
    http_status: Optional[int] = None
    attempts_log: List[Dict[str, Any]] = field(default_factory=list)
    raw_content: str = field(default="", repr=False)
    prompt_text: str = field(default="", repr=False)
    #: 每次专项调用的可回放输入/原始响应；只在 AI_AUDIT_TRACE 开启时写入任务目录。
    raw_exchanges: List[Dict[str, Any]] = field(default_factory=list, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "prompt_versions": list(self.prompt_versions),
            "finish_reason": self.finish_reason,
            "token_usage": dict(self.token_usage or {}),
            "input_chars": self.input_chars,
            "raw_count": self.raw_count,
            "accepted_count": self.accepted_count,
            "rejected_count": self.rejected_count,
            "rejection_reasons": dict(self.rejection_reasons),
            "response_sha256": self.response_sha256,
            "response_chars": self.response_chars,
            "fallback_used": self.fallback_used,
            "fallback_reason": self.fallback_reason,
            "specialty": self.specialty,
            "calls": [dict(call) for call in self.calls],
            "errors": [dict(error) for error in self.errors],
            "attempts": int(self.attempts or 1),
            "retry_count": int(self.retry_count or 0),
            "retried": bool(self.retried),
            "retryable": bool(self.retryable),
            "error_type": self.error_type,
            "http_status": self.http_status,
            "attempts_log": [dict(item) for item in self.attempts_log],
        }


class AuditIssueList(list):
    """list-compatible return value carrying the audit outcome.

    AIFindingsService 与既有 mock 仍按 list 消费；真实调用可从 ``outcome``
    读取逐窗口校验状态，避免“空列表”掩盖非法响应/截断。
    """

    def __init__(self, issues: Optional[List[Dict[str, Any]]] = None, outcome: Optional[AuditOutcome] = None):
        super().__init__(issues or [])
        self.outcome = outcome


def _combine_outcomes(outcomes: List[AuditOutcome]) -> AuditOutcome:
    combined = AuditOutcome()
    if not outcomes:
        combined.status = "not_run"
        return combined
    combined.issues = [issue for outcome in outcomes for issue in outcome.issues]
    combined.accepted_count = sum(outcome.accepted_count for outcome in outcomes)
    combined.raw_count = sum(outcome.raw_count for outcome in outcomes)
    combined.rejected_count = sum(outcome.rejected_count for outcome in outcomes)
    combined.input_chars = sum(outcome.input_chars for outcome in outcomes)
    combined.response_chars = sum(outcome.response_chars for outcome in outcomes)
    combined.fallback_used = any(outcome.fallback_used for outcome in outcomes)
    combined.fallback_reason = next(
        (outcome.fallback_reason for outcome in outcomes if outcome.fallback_reason), None
    )
    for outcome in outcomes:
        for key, value in outcome.rejection_reasons.items():
            combined.rejection_reasons[key] = combined.rejection_reasons.get(key, 0) + value
        combined.prompt_versions.extend(outcome.prompt_versions)
        combined.calls.extend(outcome.calls)
        combined.errors.extend(outcome.errors)
        combined.raw_exchanges.extend(outcome.raw_exchanges)
        combined.attempts_log.extend(outcome.attempts_log)
        combined.retry_count += int(outcome.retry_count or 0)
        combined.retried = combined.retried or bool(outcome.retried)
        combined.retryable = combined.retryable or bool(outcome.retryable)
    combined.attempts = sum(max(1, int(outcome.attempts or 1)) for outcome in outcomes)
    statuses = [outcome.status for outcome in outcomes]
    if all(status == "ok" for status in statuses):
        combined.status = "ok"
    elif all(status in {"ok", "empty"} for status in statuses) and "ok" in statuses:
        combined.status = "ok"
    elif all(status == "empty" for status in statuses):
        combined.status = "empty"
    elif any(status == "error" for status in statuses) and not any(
        status in {"ok", "empty", "partial"} for status in statuses
    ):
        combined.status = "error"
    elif any(status == "invalid" for status in statuses) and not any(
        status in {"ok", "empty", "partial"} for status in statuses
    ):
        combined.status = "invalid"
    elif any(status == "truncated" for status in statuses) and not any(
        status in {"ok", "empty", "partial"} for status in statuses
    ):
        combined.status = "truncated"
    else:
        combined.status = "partial"
    first_call = next((call for call in combined.calls if call.get("provider") or call.get("model")), {})
    combined.provider = first_call.get("provider")
    combined.model = first_call.get("model")
    combined.finish_reason = first_call.get("finish_reason")
    combined.token_usage = first_call.get("token_usage")
    combined.prompt_version = first_call.get("prompt_version")
    return combined



@dataclass
class ExtractorConfig:
    """抽取器配置"""
    url: str = field(default_factory=lambda: _read_str_env("AI_EXTRACTOR_URL", DEFAULT_EXTRACTOR_URL))
    enabled: bool = field(default_factory=lambda: _read_bool_env("AI_ASSIST_ENABLED", False))
    direct_only: bool = False
    max_tokens: Optional[int] = None
    main_model: str = field(default_factory=lambda: _read_str_env("AI_MAIN_MODEL", _read_str_env("AI_EXTRACTOR_MODEL")))
    main_provider: str = field(default_factory=lambda: _read_str_env("AI_MAIN_PROVIDER"))
    audit_provider: str = field(
        default_factory=lambda: _read_str_env(
            "AI_AUDIT_PROVIDER",
            _read_str_env("AI_MAIN_PROVIDER", "gemini_main"),
        )
    )
    audit_model: str = field(
        default_factory=lambda: _read_str_env(
            "AI_AUDIT_MODEL",
            _read_str_env("AI_MAIN_MODEL", _read_str_env("AI_LOCATOR_MODEL")),
        )
    )
    direct_fallback: bool = field(default_factory=lambda: _read_bool_env("AI_EXTRACTOR_DIRECT_FALLBACK", True))
    timeout: float = field(default_factory=lambda: _read_float_env("AI_EXTRACTOR_TIMEOUT", REQUEST_TIMEOUT))
    max_retries: int = field(default_factory=lambda: int(os.getenv("AI_EXTRACTOR_MAX_RETRIES", str(MAX_RETRIES))))
    retry_delay: float = field(default_factory=lambda: _read_float_env("AI_EXTRACTOR_RETRY_DELAY", RETRY_DELAY))
    #: 专项×窗口级补试次数（本轮默认最多 1 次）。审计调用会关闭 provider
    #: 内层重试，由该值统一控制，避免两层重试叠加。
    specialty_retries: int = field(
        default_factory=lambda: _read_int_env("AI_AUDIT_UNIT_RETRIES", 1, 0, 2)
    )
    specialty_retry_delay: float = field(
        default_factory=lambda: _read_float_env("AI_AUDIT_UNIT_RETRY_DELAY", 1.5)
    )

class ExtractorClient:
    """AI抽取器客户端"""

    def __init__(self, config: Optional[ExtractorConfig] = None):
        self.config = config or ExtractorConfig()
        self._direct_ai_client: Optional[AIClient] = None
        # 调用留痕：每次真实模型调用（含失败）都追加一条记录，供
        # ai_execution 状态机判定"AI 是否真的执行过"。无留痕时禁止
        # 呈现为 AI 已完成（P0 假完成修复，见 src/services/ai_execution.py）。
        self.call_ledger: List[Dict[str, Any]] = []

    def record_call(
        self,
        provider: Optional[str],
        model: Optional[str],
        *,
        prompt_version: Optional[str] = None,
        finish_reason: Optional[str] = None,
        token_usage: Optional[Dict[str, Any]] = None,
        content: str = "",
        error: Optional[str] = None,
        contract_ok: Optional[bool] = None,
        specialty: Optional[str] = None,
        attempt: int = 1,
        error_type: Optional[str] = None,
        http_status: Optional[int] = None,
        retryable: Optional[bool] = None,
    ) -> None:
        """追加一条调用留痕。error 非空表示本次调用失败。"""

        self.call_ledger.append(
            {
                "provider": provider,
                "model": model,
                "prompt_version": prompt_version,
                "finish_reason": finish_reason,
                "token_usage": token_usage,
                # ledger 数据契约（2026-09-06 GPT5.6 复核整改）：
                # ``content`` 是 ai_execution 状态机的判定证据（_call_succeeded
                # 要求非空正文），此前只落 content_length 导致真实成功调用
                # 必被判 ai_empty_response；``content_length`` 保留为轻量
                # 冗余字段供展示层使用。content 可能含材料原文，仅供
                # 进程内状态机消费，pop 后不再外带。
                "content": content or "",
                "content_length": len(content or ""),
                # 合法 JSON 不等于合法业务响应：结构/字段/引文校验失败时由
                # 调用方置 False，状态机不得把它当“成功且无问题”。
                "contract_ok": contract_ok,
                "error": error,
                "specialty": specialty,
                "attempt": int(attempt or 1),
                "error_type": error_type,
                "http_status": http_status,
                "retryable": retryable,
                "timestamp": time.time(),
            }
        )

    def pop_call_ledger(self) -> List[Dict[str, Any]]:
        """取出并清空调用留痕（供上层聚合进 result.meta）。"""

        ledger, self.call_ledger = self.call_ledger, []
        return ledger

    def _get_direct_ai_client(self) -> AIClient:
        if self._direct_ai_client is None:
            self._direct_ai_client = AIClient()
        return self._direct_ai_client

    def _get_preferred_provider(self) -> Optional[str]:
        provider = (self.config.main_provider or "").strip()
        return provider or None

    def _get_audit_provider(self) -> Optional[str]:
        provider = (self.config.audit_provider or "").strip()
        if provider:
            return provider
        return self._get_preferred_provider()

    def _get_audit_model(self) -> Optional[str]:
        model = (self.config.audit_model or "").strip()
        return model or None

    @staticmethod
    def _extract_json_array(text: str) -> Optional[List[Dict[str, Any]]]:
        raw = (text or "").strip()
        if not raw:
            return None
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return data
        except Exception:
            pass
        # Try fenced JSON first
        m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", raw, re.S | re.I)
        if m:
            try:
                data = json.loads(m.group(1))
                if isinstance(data, list):
                    return data
            except Exception:
                pass
        # Fallback to first array-shaped snippet
        m = re.search(r"(\[.*\])", raw, re.S)
        if m:
            try:
                data = json.loads(m.group(1))
                if isinstance(data, list):
                    return data
            except Exception:
                pass
        return None

    @staticmethod
    def _response_content_text(response: Any) -> str:
        if isinstance(response, dict):
            return str(response.get("content", "") or "")
        if isinstance(response, str):
            return response
        if isinstance(response, list):
            chunks: List[str] = []
            for item in response:
                if isinstance(item, dict):
                    content = item.get("content") or item.get("text")
                    if content is not None:
                        chunks.append(str(content))
                elif isinstance(item, str):
                    chunks.append(item)
            if chunks:
                return "\n".join(chunks)
            try:
                return json.dumps(response, ensure_ascii=False)
            except Exception:
                return str(response)
        return str(response or "")

    @staticmethod
    def _simple_pair_extract(text: str) -> List[Dict[str, Any]]:
        """Local lightweight extractor used when extractor service is unreachable."""
        hits: List[Dict[str, Any]] = []
        pattern = re.compile(
            r"(预算|年初预算|一般公共预算)[^\d]{0,12}(\d+(?:\.\d+)?)"
            r".{0,40}?"
            r"(决算|本年支出|支出合计)[^\d]{0,12}(\d+(?:\.\d+)?)",
            re.S,
        )
        for match in pattern.finditer(text or ""):
            budget_text = match.group(2)
            final_text = match.group(4)
            stmt_text = "文本比较语句"
            budget_start = match.start(2)
            final_start = match.start(4)
            stmt_start = match.start()
            clip_start = max(0, match.start() - 30)
            clip_end = min(len(text), match.end() + 30)
            clip = text[clip_start:clip_end]
            hits.append(
                {
                    "budget_text": budget_text,
                    "budget_span": [budget_start, budget_start + len(budget_text)],
                    "final_text": final_text,
                    "final_span": [final_start, final_start + len(final_text)],
                    "stmt_text": stmt_text,
                    "stmt_span": [stmt_start, stmt_start + len(stmt_text)],
                    "reason_text": None,
                    "reason_span": None,
                    "item_title": None,
                    "clip": clip,
                }
            )
            if len(hits) >= 20:
                break
        return hits

    @staticmethod
    @staticmethod
    def _normalize_confidence(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            if isinstance(value, str):
                raw = value.strip()
                if not raw:
                    return None
                if raw.endswith("%"):
                    return float(raw[:-1]) / 100.0
                return float(raw)
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _normalize_severity(value: Any) -> str:
        raw = str(value or "").strip().lower()
        mapping = {
            "critical": "critical",
            "fatal": "critical",
            "p0": "high",
            "error": "high",
            "high": "high",
            "p1": "medium",
            "warn": "medium",
            "warning": "medium",
            "medium": "medium",
            "manual_review": "manual_review",
            "p2": "low",
            "low": "low",
            "info": "info",
            "hint": "info",
        }
        return mapping.get(raw, "medium")

    @staticmethod
    def _normalize_page(page_value: Any) -> Optional[int]:
        if isinstance(page_value, bool):
            return None
        if isinstance(page_value, int):
            return page_value if page_value > 0 else None
        if isinstance(page_value, float):
            parsed = int(page_value)
            return parsed if parsed > 0 else None
        if isinstance(page_value, str):
            token = page_value.strip()
            if not token:
                return None
            if token.isdigit():
                parsed = int(token)
                return parsed if parsed > 0 else None
        return None

    def _resolve_audit_max_tokens(self) -> int:
        """审计调用 max_tokens 解析链：AI_AUDIT_MAX_TOKENS > AI_MAX_TOKENS > 应用配置。

        此前写死 3200：复杂决算长文一旦截断，空结果会被误解为"未发现问题"。
        应用配置读真实 Settings 接口（config/app.yaml 的 ai.max_tokens）。
        """

        if self.config.max_tokens is not None:
            return self.config.max_tokens
        for env_name in ("AI_AUDIT_MAX_TOKENS", "AI_MAX_TOKENS"):
            raw = os.getenv(env_name)
            if raw is None:
                continue
            try:
                value = int(str(raw).strip())
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
        try:
            from config.settings import get_settings

            configured = get_settings().get("ai", "max_tokens")
            value = int(configured)
            if value > 0:
                return value
        except Exception:
            pass
        return 4000

    @staticmethod
    def _safe_error_text(exc: BaseException) -> str:
        """只保留异常类型与指纹，避免把可能回显的材料原文落进任务元数据。"""
        return f"{type(exc).__name__}:{fingerprint_for_log(str(exc) or type(exc).__name__)}"

    @staticmethod
    def _error_fingerprint(exc: BaseException) -> str:
        return fingerprint_for_log(str(exc) or type(exc).__name__)

    def _record_specialty_failure(
        self,
        outcome: AuditOutcome,
        *,
        specialty: str,
        attempt: int,
        prompt: str,
        error_type: str,
        status_code: Optional[int],
        retryable: bool,
        public_error: str,
        response_excerpt: str = "",
    ) -> AuditOutcome:
        provider = self._get_audit_provider()
        model = self._get_audit_model()
        outcome.status = "error"
        outcome.provider = provider
        outcome.model = model
        outcome.retryable = bool(retryable)
        outcome.error_type = error_type
        outcome.http_status = status_code
        outcome.errors.append(
            {
                "specialty": specialty,
                "reason": "call_failed",
                "error": public_error,
                "error_type": error_type,
                "http_status": status_code,
                "retryable": bool(retryable),
                "attempt": attempt,
            }
        )
        outcome.attempts_log.append(
            {
                "attempt": attempt,
                "status": "error",
                "error_type": error_type,
                "http_status": status_code,
                "retryable": bool(retryable),
                "error": public_error,
                "response_chars": len(response_excerpt or ""),
                "response_sha256": (
                    hashlib.sha256(response_excerpt.encode("utf-8")).hexdigest()
                    if response_excerpt
                    else None
                ),
            }
        )
        outcome.attempts = len(outcome.attempts_log)
        self.record_call(
            provider,
            model,
            prompt_version=outcome.prompt_version,
            error=public_error,
            specialty=specialty,
            attempt=attempt,
            error_type=error_type,
            http_status=status_code,
            retryable=bool(retryable),
        )
        outcome.calls.append(self._call_summary(outcome))
        outcome.raw_exchanges.append(
            {
                "specialty": specialty,
                "attempt": attempt,
                "prompt": prompt,
                "raw_response": "",
                "error": {
                    "error_type": error_type,
                    "http_status": status_code,
                    "retryable": bool(retryable),
                    "message": public_error,
                    # 仅供任务目录私有 trace 回放，不进入公开 API。
                    "response_excerpt": response_excerpt,
                },
            }
        )
        return outcome

    async def _run_direct_specialty(
        self,
        section_text: str,
        structured_context: Optional[str],
        specialty: str,
        *,
        attempt: int = 1,
    ) -> AuditOutcome:
        instructions = render_specialty_instructions(specialty, MIN_ISSUE_CONFIDENCE)
        prompt_version = specialty_prompt_version(specialty, MIN_ISSUE_CONFIDENCE)
        prompt = f"{instructions}\n\n{structured_context or ''}\n\n待审文本：\n{section_text or ''}"
        outcome = AuditOutcome(
            specialty=specialty,
            prompt_version=prompt_version,
            prompt_versions=[prompt_version],
            input_chars=len(section_text or ""),
            prompt_text=prompt,
            provider=self._get_audit_provider(),
            model=self._get_audit_model(),
        )
        ai_client = self._get_direct_ai_client()
        try:
            # 内层 provider 重试关闭；专项级补试由 _run_direct_specialty_with_retry
            # 统一控制，避免同一次失败被两层各重试一次。
            response = await ai_client.chat(
                messages=[
                    {"role": "system", "content": "你是严格的 JSON 输出助手。"},
                    {"role": "user", "content": prompt},
                ],
                preferred_provider=self._get_audit_provider(),
                model=self._get_audit_model(),
                temperature=0,
                max_tokens=self._resolve_audit_max_tokens(),
                timeout=int(self.config.timeout),
                max_retries_override=0,
            )
        except LLMError as exc:
            error_type = str(getattr(exc.error_type, "value", exc.error_type) or "unknown")
            response_excerpt = sanitize_error_excerpt(
                getattr(exc, "response_excerpt", "") or ""
            )
            return self._record_specialty_failure(
                outcome,
                specialty=specialty,
                attempt=attempt,
                prompt=prompt,
                error_type=error_type,
                status_code=getattr(exc, "status_code", None),
                retryable=bool(getattr(exc, "is_retryable", False)),
                public_error=(
                    f"{error_type}:HTTP{getattr(exc, 'status_code', None)}:"
                    f"{self._error_fingerprint(exc)}"
                    if getattr(exc, "status_code", None)
                    else f"{error_type}:{self._error_fingerprint(exc)}"
                ),
                response_excerpt=response_excerpt,
            )
        except Exception as exc:
            return self._record_specialty_failure(
                outcome,
                specialty=specialty,
                attempt=attempt,
                prompt=prompt,
                error_type=type(exc).__name__,
                status_code=None,
                retryable=False,
                public_error=self._safe_error_text(exc),
            )

        content_text = self._response_content_text(response)
        finish_reason = response.get("finish_reason") if isinstance(response, dict) else None
        provider = response.get("provider_used") if isinstance(response, dict) else None
        model = response.get("model") if isinstance(response, dict) else None
        token_usage = response.get("tokens") if isinstance(response, dict) else None
        self.record_call(
            provider,
            model,
            prompt_version=prompt_version,
            finish_reason=finish_reason if isinstance(finish_reason, str) else None,
            token_usage=token_usage,
            content=content_text,
            specialty=specialty,
            attempt=attempt,
        )
        provenance = build_finding_provenance(
            model_version=build_model_version(provider, model),
            prompt_version=prompt_version,
            source_channel="direct_llm",
        )
        outcome.provider = provider
        outcome.model = model
        outcome.finish_reason = finish_reason if isinstance(finish_reason, str) else None
        outcome.token_usage = token_usage if isinstance(token_usage, dict) else None
        outcome.raw_content = content_text
        outcome.response_chars = len(content_text)
        outcome.response_sha256 = hashlib.sha256(content_text.encode("utf-8")).hexdigest()
        outcome.raw_exchanges.append(
            {
                "specialty": specialty,
                "attempt": attempt,
                "prompt": prompt,
                "raw_response": content_text,
            }
        )
        truncated = str(finish_reason or "").strip().lower() in {"length", "max_tokens", "incomplete"}
        parsed = self._extract_json_array(content_text)
        if parsed is None:
            outcome.status = "truncated" if truncated else "invalid"
            outcome.error_type = "response_truncated" if truncated else "response_invalid"
            outcome.retryable = False
            outcome.rejection_reasons["json_parse_failed"] = 1
            self.call_ledger[-1]["contract_ok"] = False
            outcome.attempts_log.append(
                {
                    "attempt": attempt,
                    "status": outcome.status,
                    "error_type": outcome.error_type,
                    "retryable": False,
                    "response_chars": outcome.response_chars,
                    "response_sha256": outcome.response_sha256,
                    "finish_reason": outcome.finish_reason,
                }
            )
            outcome.attempts = len(outcome.attempts_log)
            outcome.calls.append(self._call_summary(outcome))
            return outcome
        validation = validate_audit_items(
            parsed,
            window_text=section_text,
            min_confidence=MIN_ISSUE_CONFIDENCE,
            truncated=truncated,
        )
        self.call_ledger[-1]["contract_ok"] = validation.status in {"ok", "empty"}
        outcome.status = validation.status
        outcome.raw_count = validation.raw_count
        outcome.accepted_count = validation.accepted_count
        outcome.rejected_count = validation.rejected_count
        outcome.rejection_reasons = dict(validation.reasons)
        for accepted in validation.accepted:
            issue = dict(accepted)
            issue["type"] = accepted["problem_type"]
            issue["quote"] = accepted["quote"]
            issue["page"] = accepted.get("page")
            issue["audit_specialty"] = specialty
            issue["provenance"] = dict(provenance)
            issue["manual_confirm"] = False
            outcome.issues.append(issue)
        if validation.status in {"invalid", "truncated"}:
            outcome.error_type = (
                "response_truncated" if validation.status == "truncated" else "response_invalid"
            )
            outcome.errors.append(
                {
                    "specialty": specialty,
                    "reason": "response_rejected_or_truncated",
                    "status": validation.status,
                    "error_type": outcome.error_type,
                    "retryable": False,
                    "attempt": attempt,
                    "raw_count": validation.raw_count,
                    "rejected_count": validation.rejected_count,
                    "rejection_reasons": dict(validation.reasons),
                }
            )
        elif validation.status == "partial":
            outcome.errors.append(
                {
                    "specialty": specialty,
                    "reason": "partial_response",
                    "status": validation.status,
                    "retryable": False,
                    "attempt": attempt,
                    "raw_count": validation.raw_count,
                    "rejected_count": validation.rejected_count,
                    "rejection_reasons": dict(validation.reasons),
                }
            )
        outcome.attempts_log.append(
            {
                "attempt": attempt,
                "status": outcome.status,
                "provider": outcome.provider,
                "model": outcome.model,
                "finish_reason": outcome.finish_reason,
                "token_usage": dict(outcome.token_usage or {}),
                "accepted_count": outcome.accepted_count,
                "rejected_count": outcome.rejected_count,
                "response_chars": outcome.response_chars,
                "response_sha256": outcome.response_sha256,
                "retryable": False,
            }
        )
        outcome.attempts = len(outcome.attempts_log)
        outcome.calls.append(self._call_summary(outcome))
        return outcome

    async def _run_direct_specialty_with_retry(
        self,
        section_text: str,
        structured_context: Optional[str],
        specialty: str,
    ) -> AuditOutcome:
        """执行一个专项×窗口检查单元，可恢复上游故障最多补试一次。"""

        outcome = await self._run_direct_specialty(
            section_text, structured_context, specialty, attempt=1
        )
        retries = max(0, int(self.config.specialty_retries or 0))
        while (
            outcome.status == "error"
            and outcome.retryable
            and int(outcome.retry_count or 0) < retries
        ):
            delay = max(0.0, float(self.config.specialty_retry_delay or 0))
            if delay:
                await asyncio.sleep(delay)
            retried = await self._run_direct_specialty(
                section_text,
                structured_context,
                specialty,
                attempt=int(outcome.attempts or 1) + 1,
            )
            retried.attempts_log = [*outcome.attempts_log, *retried.attempts_log]
            retried.raw_exchanges = [*outcome.raw_exchanges, *retried.raw_exchanges]
            retried.attempts = len(retried.attempts_log)
            retried.retry_count = int(outcome.retry_count or 0) + 1
            retried.retried = True
            if retried.status == "error":
                # 只公开最终失败一条；逐次尝试留在 attempts_log 与私有 trace。
                retried.errors = [{**retried.errors[-1], "retry_history": [
                    {
                        "attempt": item.get("attempt"),
                        "status": item.get("status"),
                        "error_type": item.get("error_type"),
                        "http_status": item.get("http_status"),
                        "error": item.get("error"),
                    }
                    for item in retried.attempts_log
                ]}] if retried.errors else []
                retried.retryable = bool(retried.retryable)
            else:
                retried.errors = []
                retried.retryable = False
                retried.error_type = None
                retried.http_status = None
            if retried.calls:
                retried.calls[-1]["attempts"] = retried.attempts
                retried.calls[-1]["retry_count"] = retried.retry_count
                retried.calls[-1]["retried"] = True
            outcome = retried
        outcome.attempts = max(1, len(outcome.attempts_log) or int(outcome.attempts or 1))
        return outcome

    @staticmethod
    def _call_summary(outcome: AuditOutcome) -> Dict[str, Any]:
        return {
            "specialty": outcome.specialty,
            "status": outcome.status,
            "provider": outcome.provider,
            "model": outcome.model,
            "prompt_version": outcome.prompt_version,
            "finish_reason": outcome.finish_reason,
            "token_usage": dict(outcome.token_usage or {}),
            "input_chars": outcome.input_chars,
            "raw_count": outcome.raw_count,
            "accepted_count": outcome.accepted_count,
            "rejected_count": outcome.rejected_count,
            "rejection_reasons": dict(outcome.rejection_reasons),
            "response_sha256": outcome.response_sha256,
            "response_chars": outcome.response_chars,
            "contract_mode": "strict_specialty_v1",
            "attempts": int(outcome.attempts or 1),
            "retry_count": int(outcome.retry_count or 0),
            "retried": bool(outcome.retried),
            "retryable": bool(outcome.retryable),
            "error_type": outcome.error_type,
            "http_status": outcome.http_status,
        }

    async def _direct_semantic_audit(
        self,
        section_text: str,
        structured_context: Optional[str] = None,
        *,
        specialties: Optional[List[str]] = None,
    ) -> AuditIssueList:
        """Run the configured specialties directly and return issues + outcome."""
        selected = specialties or list(SPECIALTIES)
        outcomes = [
            await self._run_direct_specialty_with_retry(
                section_text, structured_context, specialty
            )
            for specialty in selected
        ]
        combined = _combine_outcomes(outcomes)
        return AuditIssueList(combined.issues, combined)

    async def audit_full_report_outcome(
        self,
        section_text: str,
        doc_hash: str,
        structured_context: Optional[str] = None,
        *,
        specialties: Optional[List[str]] = None,
    ) -> AuditOutcome:
        """Primary AI audit entry with explicit execution status.

        策略：
        - 直连专项调用是主路径；合法空数组是“有效检查无发现”，不再触发第二路；
        - 仅当直连**调用失败**（不是空、不是非法结构）且允许回退时，才走抽取服务；
        - 回退来源、提示词/任务契约与成本逐次记录，不把两路结果混为一次成功。
        """
        if not self.config.enabled:
            return AuditOutcome(status="disabled")
        if not (section_text or "").strip():
            return AuditOutcome(status="empty_input", input_chars=0)

        direct = await self._direct_semantic_audit(
            section_text, structured_context=structured_context, specialties=specialties
        )
        outcome = getattr(direct, "outcome", None)
        if outcome is None:
            outcome = AuditOutcome(
                status="ok" if list(direct) else "empty",
                issues=list(direct),
                accepted_count=len(list(direct)),
            )
        elif not outcome.issues and list(direct):
            outcome.issues = list(direct)
            outcome.accepted_count = len(outcome.issues)
            if outcome.status in {"error", "not_run"}:
                outcome.status = "ok"
        if outcome.status != "error":
            return outcome
        if not self.config.direct_fallback or self.config.direct_only:
            return outcome

        service = await self.semantic_service_outcome(section_text, doc_hash)
        service.fallback_used = True
        service.fallback_reason = "direct_call_failed"
        service.errors = [*outcome.errors, *service.errors]
        service.calls = [*outcome.calls, *service.calls]
        return service

    async def ai_extract_pairs(self, section_text: str, doc_hash: str) -> List[Dict[str, Any]]:
        """
        调用AI抽取器进行信息抽取
        
        Args:
            section_text: （三）小节全文
            doc_hash: 文档哈希
            
        Returns:
            抽取结果列表，每个元素包含：
            - budget_text: 预算数字原文
            - budget_span: 预算数字span [start, end)
            - final_text: 决算数字原文  
            - final_span: 决算数字span [start, end)
            - stmt_text: 比较语句原文
            - stmt_span: 比较语句span [start, end)
            - reason_text: 原因说明原文（可选）
            - reason_span: 原因说明span（可选）
            - item_title: 项目标题（可选）
            - clip: 原文截取片段
        """
        if not self.config.enabled:
            logger.debug("AI辅助未启用，返回空列表")
            return []
            
        if not section_text.strip():
            logger.debug("输入文本为空，返回空列表")
            return []
            
        try:
            result = await self._call_with_retry(section_text, doc_hash)
            return result
            
        except Exception as e:
            logger.error(f"AI抽取失败: {e}")
            # 网络失败时不影响主流程，退回纯规则模式
            return []
    
    async def _call_with_retry(self, section_text: str, doc_hash: str) -> List[Dict[str, Any]]:
        """带重试的调用"""
        last_exception = None
        
        for attempt in range(self.config.max_retries + 1):
            try:
                if attempt > 0:
                    logger.info(f"AI抽取重试第{attempt}次")
                    await asyncio.sleep(self.config.retry_delay * attempt)
                    
                return await self._single_call(section_text, doc_hash)
                
            except httpx.TimeoutException as e:
                last_exception = e
                logger.warning(f"AI抽取超时 (尝试 {attempt + 1}/{self.config.max_retries + 1}): {e}")
                
            except httpx.ConnectError as e:
                last_exception = e
                logger.warning(f"AI抽取连接失败 (尝试 {attempt + 1}/{self.config.max_retries + 1}): {e}")
                
            except Exception as e:
                last_exception = e
                logger.warning(f"AI抽取异常 (尝试 {attempt + 1}/{self.config.max_retries + 1}): {e}")
                
        # 所有重试都失败
        logger.error(f"AI抽取最终失败: {last_exception}")
        if self.config.direct_fallback:
            logger.warning("Extractor service unreachable, falling back to local pair extraction")
            return self._simple_pair_extract(section_text)
        raise last_exception
    
    async def _single_call(self, section_text: str, doc_hash: str) -> List[Dict[str, Any]]:
        """单次调用"""
        request_data = {
            "task": "R33110_pairs_v1",
            "section_text": section_text,
            "language": "zh", 
            "doc_hash": doc_hash,
            "max_windows": 3
        }
        if self.config.main_model:
            request_data["model"] = self.config.main_model
        
        async with httpx.AsyncClient(timeout=self.config.timeout) as client:
            response = await client.post(
                self.config.url,
                json=request_data,
                headers={"Content-Type": "application/json"}
            )
            
            if response.status_code != 200:
                # 只带状态码 + 响应体指纹：响应体里回显了送检的材料原文，
                # 而这个异常消息会被上游 `logger.error("...%s", e)` 落盘。
                raise Exception(
                    "AI抽取器返回错误状态码: {status}, 响应指纹: {marker}".format(
                        status=response.status_code,
                        marker=fingerprint_for_log(response.text),
                    )
                )

            result = response.json()

            if "hits" not in result:
                raise Exception(
                    f"AI抽取器返回格式错误: 缺少 hits 字段, 响应指纹: {fingerprint_for_log(result)}"
                )
                
            hits = result["hits"]
            logger.info(f"AI抽取成功，获得{len(hits)}个结果")
            
            # 转换为内部格式
            return self._convert_hits_to_internal_format(hits)
    
    def _convert_hits_to_internal_format(self, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """将API返回的hits转换为内部格式"""
        converted = []
        
        for hit in hits:
            try:
                # 验证必需字段
                required_fields = ["budget_text", "budget_span", "final_text", "final_span", "stmt_text", "stmt_span", "clip"]
                # 日志只记"缺了哪些字段名"+ 整条 hit 的指纹：hit 里的 budget_text /
                # final_text / stmt_text 就是送检材料原文，而 message 字段不经脱敏，
                # 把 hit 整体拼进去等于把原文落盘（独立复核在此发现真实泄漏）。
                missing_fields = [field for field in required_fields if field not in hit]
                if missing_fields:
                    logger.warning(
                        "跳过缺少必需字段的hit: missing=%s, %s",
                        sorted(missing_fields),
                        fingerprint_for_log(hit),
                    )
                    continue
                    
                # 验证span格式。
                # 这里必须先把三个 span 全判完再决策：早先的写法是在内层 for 里
                # `continue`，那只是跳到下一个 span 字段，坏 hit 依然会走到
                # `converted.append(hit)`——与日志里写的"跳过"完全相反。
                # 取舍：span 是必需字段，形状不对说明这条 AI 响应违反了约定，
                # 同一条响应里的其它字段同样不可信，因此整条丢弃（与上面"缺必需
                # 字段"分支一致）；代价是可能少一个候选问题，换来的是不把来源
                # 可疑的证据放进审核结果。可选的 reason_span 才走"置空但保留"。
                invalid_span_field = next(
                    (
                        field
                        for field in ("budget_span", "final_span", "stmt_span")
                        if not isinstance(hit[field], list) or len(hit[field]) != 2
                    ),
                    None,
                )
                if invalid_span_field is not None:
                    logger.warning(
                        "跳过span格式错误的hit: field=%s, span_type=%s, %s",
                        invalid_span_field,
                        type(hit[invalid_span_field]).__name__,
                        fingerprint_for_log(hit),
                    )
                    continue
                        
                # 处理可选的reason_span
                reason_span = hit.get("reason_span")
                if reason_span and (not isinstance(reason_span, list) or len(reason_span) != 2):
                    # reason_span 本应是 [start, end) 两个整数；走到这里说明 AI 返回的
                    # 形状不对，此时它可能是任意内容（含原文片段），只记类型与指纹。
                    logger.warning(
                        "reason_span格式错误，设为None: type=%s, %s",
                        type(reason_span).__name__,
                        fingerprint_for_log(reason_span),
                    )
                    hit["reason_span"] = None
                    hit["reason_text"] = None
                
                converted.append(hit)
                
            except Exception as e:
                logger.warning(
                    "转换hit失败: %s, %s",
                    describe_exception(e),
                    fingerprint_for_log(hit),
                )
                continue
                
        return converted
    
    async def ai_semantic_audit(
        self,
        section_text: str,
        doc_hash: str,
        structured_context: Optional[str] = None,
    ) -> AuditIssueList:
        """抽取服务语义审计（legacy 兼容入口）。

        合法空 hits 是“有效检查无发现”，不再触发直连第二路；只有服务调用失败
        且允许回退时才走直连专项。来源与提示词契约在 outcome 中如实记录。
        """
        if not self.config.enabled:
            return AuditIssueList([], AuditOutcome(status="disabled"))
        if not section_text.strip():
            return AuditIssueList([], AuditOutcome(status="empty_input"))
        service = await self.semantic_service_outcome(section_text, doc_hash)
        if service.status != "error" or not self.config.direct_fallback:
            return AuditIssueList(service.issues, service)
        direct = await self._direct_semantic_audit(
            section_text, structured_context=structured_context
        )
        outcome = getattr(direct, "outcome", None)
        if outcome is None:
            outcome = AuditOutcome(
                status="ok" if list(direct) else "empty",
                issues=list(direct),
                accepted_count=len(list(direct)),
            )
        elif not outcome.issues and list(direct):
            outcome.issues = list(direct)
            outcome.accepted_count = len(outcome.issues)
            if outcome.status in {"error", "not_run"}:
                outcome.status = "ok"
        outcome.fallback_used = True
        outcome.fallback_reason = "service_call_failed"
        outcome.errors = [*service.errors, *outcome.errors]
        outcome.calls = [*service.calls, *outcome.calls]
        return AuditIssueList(outcome.issues, outcome)

    async def ai_full_report_audit(
        self,
        section_text: str,
        doc_hash: str,
        structured_context: Optional[str] = None,
    ) -> AuditIssueList:
        """全量报告审查（兼容入口）：返回 list，同时携带 AuditOutcome。"""
        outcome = await self.audit_full_report_outcome(
            section_text, doc_hash, structured_context=structured_context
        )
        return AuditIssueList(outcome.issues, outcome)

    async def semantic_service_outcome(
        self, section_text: str, doc_hash: str
    ) -> AuditOutcome:
        outcome = AuditOutcome(
            status="not_run",
            input_chars=len(section_text or ""),
            provider="extractor_service",
            model=self.config.main_model or None,
            prompt_version=SEMANTIC_AUDIT_SERVICE_TASK,
            prompt_versions=[SEMANTIC_AUDIT_SERVICE_TASK],
        )
        request_data = {
            "task": SEMANTIC_AUDIT_SERVICE_TASK,
            "section_text": section_text,
            "language": "zh",
            "doc_hash": doc_hash,
            "max_windows": 3,
        }
        if self.config.main_model:
            request_data["model"] = self.config.main_model
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout) as client:
                response = await client.post(
                    self.config.url,
                    json=request_data,
                    headers={"Content-Type": "application/json"},
                )
                if response.status_code != 200:
                    raise Exception(
                        "AI语义审计返回错误状态码: {status}, 响应指纹: {marker}".format(
                            status=response.status_code,
                            marker=fingerprint_for_log(response.text),
                        )
                    )
                result = response.json()
                if "hits" not in result:
                    raise Exception(
                        "AI语义审计返回格式错误: 缺少 hits 字段, 响应指纹: {marker}".format(
                            marker=fingerprint_for_log(result)
                        )
                    )
                hits = result["hits"]
                if not isinstance(hits, list):
                    raise Exception("AI语义审计返回格式错误: hits 不是数组")
                service_model = str(result.get("model") or "").strip()
                outcome.model = service_model or self.config.main_model or None
                content = json.dumps(hits, ensure_ascii=False)
                outcome.raw_content = content
                outcome.response_chars = len(content)
                outcome.response_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
                outcome.raw_exchanges.append(
                    {
                        "specialty": "extractor_service",
                        "prompt": json.dumps(request_data, ensure_ascii=False),
                        "raw_response": content,
                    }
                )
                outcome.token_usage = result.get("usage") if isinstance(result.get("usage"), dict) else None
                self.record_call(
                    "extractor_service",
                    outcome.model,
                    prompt_version=SEMANTIC_AUDIT_SERVICE_TASK,
                    token_usage=outcome.token_usage,
                    content=content,
                )
                provenance = build_finding_provenance(
                    model_version=build_model_version(None, outcome.model),
                    prompt_version=SEMANTIC_AUDIT_SERVICE_TASK,
                    source_channel="extractor_service",
                )
                semantic_issues: List[Dict[str, Any]] = []
                rejected = 0
                for hit in hits:
                    if not isinstance(hit, dict):
                        rejected += 1
                        continue
                    issues = hit.get("semantic_issues")
                    if not isinstance(issues, list):
                        continue
                    for issue in issues:
                        if not isinstance(issue, dict):
                            rejected += 1
                            continue
                        item = dict(issue)
                        item.setdefault("provenance", dict(provenance))
                        semantic_issues.append(item)
                outcome.issues = semantic_issues
                outcome.raw_count = len(semantic_issues) + rejected
                outcome.accepted_count = len(semantic_issues)
                outcome.rejected_count = rejected
                if rejected:
                    outcome.rejection_reasons["invalid_service_items"] = rejected
                outcome.status = "partial" if rejected and semantic_issues else (
                    "ok" if semantic_issues else ("invalid" if rejected else "empty")
                )
                if self.call_ledger:
                    self.call_ledger[-1]["contract_ok"] = outcome.status in {"ok", "empty"}
                outcome.calls.append(
                    {
                        "specialty": "extractor_service",
                        "status": outcome.status,
                        "provider": "extractor_service",
                        "model": outcome.model,
                        "prompt_version": SEMANTIC_AUDIT_SERVICE_TASK,
                        "input_chars": outcome.input_chars,
                        "raw_count": outcome.raw_count,
                        "accepted_count": outcome.accepted_count,
                        "rejected_count": outcome.rejected_count,
                        "rejection_reasons": dict(outcome.rejection_reasons),
                        "response_sha256": outcome.response_sha256,
                        "response_chars": outcome.response_chars,
                        "contract_mode": "remote_unverified",
                    }
                )
                return outcome
        except Exception as exc:
            error_text = self._safe_error_text(exc)
            outcome.status = "error"
            outcome.errors.append({"specialty": "extractor_service", "reason": "call_failed", "error": error_text})
            self.record_call(
                "extractor_service",
                self.config.main_model or None,
                prompt_version=SEMANTIC_AUDIT_SERVICE_TASK,
                error=error_text,
            )
            outcome.calls.append(
                {
                    "specialty": "extractor_service",
                    "status": "error",
                    "provider": "extractor_service",
                    "model": self.config.main_model or None,
                    "prompt_version": SEMANTIC_AUDIT_SERVICE_TASK,
                    "error": error_text,
                    "contract_mode": "remote_unverified",
                }
            )
            return outcome

    async def _call_semantic_audit(self, section_text: str, doc_hash: str) -> List[Dict[str, Any]]:
        """语义审计单次调用（兼容测试/旧调用方）。"""
        outcome = await self.semantic_service_outcome(section_text, doc_hash)
        if outcome.status == "error":
            raise RuntimeError(outcome.errors[0].get("error") if outcome.errors else "service_failed")
        return outcome.issues

    async def health_check(self) -> bool:
        """健康检查"""
        if not self.config.enabled:
            return False
            
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                health_url = self.config.url.replace("/ai/extract/v1", "/health")
                response = await client.get(health_url)
                return response.status_code == 200
                
        except Exception as e:
            logger.warning(f"AI抽取器健康检查失败: {e}")
            if self.config.direct_fallback:
                try:
                    ai_client = self._get_direct_ai_client()
                    if ai_client.get_available_providers():
                        return True
                except Exception:
                    pass
            return False

# ==================== 全局实例 ====================
_default_client = None

def get_extractor_client() -> ExtractorClient:
    """获取默认的抽取器客户端实例"""
    global _default_client
    if _default_client is None:
        _default_client = ExtractorClient()
    return _default_client

async def ai_extract_pairs(section_text: str, doc_hash: str) -> List[Dict[str, Any]]:
    """
    便捷函数：调用AI抽取器进行信息抽取
    
    Args:
        section_text: （三）小节全文
        doc_hash: 文档哈希
        
    Returns:
        抽取结果列表
    """
    client = get_extractor_client()
    return await client.ai_extract_pairs(section_text, doc_hash)

def generate_doc_hash(section_text: str) -> str:
    """生成文档哈希"""
    return hashlib.sha1(section_text.encode('utf-8')).hexdigest()

# ==================== 配置更新 ====================
def update_config(
    url: Optional[str] = None,
    enabled: Optional[bool] = None,
    main_model: Optional[str] = None,
    main_provider: Optional[str] = None,
    direct_fallback: Optional[bool] = None,
    timeout: Optional[float] = None,
    max_retries: Optional[int] = None,
    retry_delay: Optional[float] = None
):
    """更新全局配置"""
    global _default_client
    
    if _default_client is None:
        _default_client = ExtractorClient()
        
    config = _default_client.config
    
    if url is not None:
        config.url = url
    if enabled is not None:
        config.enabled = enabled
    if main_model is not None:
        config.main_model = main_model
    if main_provider is not None:
        config.main_provider = main_provider
    if direct_fallback is not None:
        config.direct_fallback = direct_fallback
    if timeout is not None:
        config.timeout = timeout
    if max_retries is not None:
        config.max_retries = max_retries
    if retry_delay is not None:
        config.retry_delay = retry_delay
        
    logger.info(f"AI抽取器配置已更新: enabled={config.enabled}, url={config.url}")
