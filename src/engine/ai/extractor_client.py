#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI抽取器客户端 - 后端调用AI抽取器微服务的客户端
"""

import os
import asyncio
import logging
from typing import List, Dict, Any, Optional
import hashlib
import json
import re
import httpx
from dataclasses import dataclass, field

from src.services.ai_client import AIClient

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


MIN_ISSUE_CONFIDENCE = _read_float_env("AI_MIN_ISSUE_CONFIDENCE", 0.75)

@dataclass
class ExtractorConfig:
    """抽取器配置"""
    url: str = field(default_factory=lambda: _read_str_env("AI_EXTRACTOR_URL", DEFAULT_EXTRACTOR_URL))
    enabled: bool = field(default_factory=lambda: _read_bool_env("AI_ASSIST_ENABLED", True))
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

class ExtractorClient:
    """AI抽取器客户端"""
    
    def __init__(self, config: Optional[ExtractorConfig] = None):
        self.config = config or ExtractorConfig()
        self._direct_ai_client: Optional[AIClient] = None

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
    def _extract_numeric_tokens(text: str) -> List[float]:
        values: List[float] = []
        for raw in re.findall(r"-?\d+(?:,\d{3})*(?:\.\d+)?", text or ""):
            try:
                values.append(float(raw.replace(",", "")))
            except Exception:
                continue
        return values

    @staticmethod
    def _should_drop_rounding_issue(
        item: Dict[str, Any],
        issue_type: str,
        original: str,
        suggestion: str,
        context: str,
    ) -> bool:
        type_hints = " ".join(
            [
                issue_type,
                str(item.get("problem_type") or ""),
                str(item.get("title") or ""),
                str(item.get("message") or ""),
            ]
        ).lower()
        if not any(
            token in type_hints
            for token in (
                "inconsist",
                "mismatch",
                "conflict",
                "勾稽",
                "不一致",
                "冲突",
                "差异",
                "口径",
            )
        ):
            return False

        original_numbers = ExtractorClient._extract_numeric_tokens(original)
        suggestion_numbers = ExtractorClient._extract_numeric_tokens(suggestion)
        if not original_numbers or not suggestion_numbers:
            return False

        content = " ".join(
            [
                original,
                suggestion,
                context,
                str(item.get("message") or ""),
                str(item.get("quote") or ""),
            ]
        )

        all_numbers = original_numbers + suggestion_numbers
        if "年" in content and all(1900 <= v <= 2100 for v in all_numbers):
            return False

        tolerance = 0.01
        if "%" in content:
            tolerance = 0.1
        elif "亿元" in content:
            tolerance = 0.0001
        elif "万元" in content:
            tolerance = 0.01
        elif "元" in content:
            tolerance = 100.0

        diff = min(abs(a - b) for a in original_numbers for b in suggestion_numbers)
        return diff <= tolerance

    @staticmethod
    def _should_drop_unverified_repeat_issue(
        section_text: str,
        issue_type: str,
        original: str,
        context: str,
    ) -> bool:
        type_hint = (issue_type or "").lower()
        if ("repeat" not in type_hint) and ("\u91cd\u590d" not in issue_type):
            return False

        anchor = (original or context or "").strip()
        if not anchor:
            return True

        anchor_norm = re.sub(r"\s+", "", anchor)
        body_norm = re.sub(r"\s+", "", section_text or "")
        if len(anchor_norm) < 24:
            return False
        return body_norm.count(anchor_norm) < 2

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

    async def _direct_semantic_audit(self, section_text: str) -> List[Dict[str, Any]]:
        """Use configured LLM provider directly when extractor service is unavailable."""
        ai_client = self._get_direct_ai_client()
        prompt = f"""
你是一名“中国政府预决算公开材料审校助手”。
输入是全文中的局部窗口文本，只能基于窗口内可直接定位的证据输出问题。

必须遵守：
1) 只输出 JSON 数组，不要输出任何其他文字；无问题返回 []。
2) 证据优先：每个问题都必须能在当前窗口内找到原文证据，不臆测、不补数、不编造页码。
3) 局部窗口限制：不要判断全书缺表、缺章、目录页码整体错误等必须看全书才能确认的问题。
4) 优先检查以下问题：
   - 同比、占比、完成率、增长率、下降率复算错误
   - 合计与明细勾稽不一致、同条说明前后金额矛盾
   - 增加/减少/增长/下降等方向描述与数字变化方向相反
   - 部门/单位预算（决算）文种误写
   - 模板残留、占位符、重复表述、残句、异常标点
   - 三公经费、政府性基金、国有资本经营无此项说明缺失
   - 年份冲突、金额单位错误、统计口径冲突、编码与名称用途错配
5) 容差：万元差值<=0.01、元差值<=100、百分点差值<=0.1 视为一致，不得报错。
6) 口径保护：政府采购预算、绩效项目资金、一般公共预算、财政拨款等不同口径，除非文本明确要求一致，否则不得直接判错。
7) OCR 噪声保护：疑似抽取残影、隐藏层重复、跨页断裂不稳定时，优先输出 manual_review 或不输出。
8) 输出必须使用中文，不得使用其他语言。

输出字段要求（每个元素必须具备）：
- problem_type: 字符串，尽量使用 ratio_recalc/sum_mismatch/document_kind_mismatch/placeholder_residue/unit_scope_conflict/duplicate_text/missing_explanation/direction_conflict/code_subject_mismatch/generic
- original: 字符串，原始问题文本或核心错误表述
- suggestion: 字符串，简短修正建议
- span: [start, end] 两个整数，无法定位填 [0,0]
- context: 字符串，包含关键证据的上下文
- severity: 仅允许 critical/high/medium/low/info/manual_review
- confidence: 0-1 数值

建议尽量补充以下字段：
- quote: 直接命中的原文片段
- page: 当前窗口内能确定页码时填写
- table_or_section: 对应表名或说明章节
- expected: 复核后的正确值
- actual: 当前文本中的值
- difference: 差额或百分点差
- check: 检查项名称，如“同比复算”“三公经费合计”
- rule_id, title, message

输出示例：
[{{
  "problem_type": "ratio_recalc",
  "original": "同比增长12.5%",
  "suggestion": "请按表内金额重新复算同比并修正文中表述",
  "span": [120, 128],
  "context": "一般公共预算支出情况说明：同比增长12.5%。",
  "severity": "high",
  "confidence": 0.86,
  "quote": "同比增长12.5%",
  "table_or_section": "一般公共预算支出情况说明",
  "expected": "8.3%",
  "actual": "12.5%",
  "difference": "4.2个百分点",
  "check": "同比复算"
}}]

低于 {MIN_ISSUE_CONFIDENCE:.2f} 置信度的问题不要输出。

待审文本：
{section_text or ""}
""".strip()
        response = await ai_client.chat(
            messages=[
                {"role": "system", "content": "你是严格的 JSON 输出助手。"},
                {"role": "user", "content": prompt},
            ],
            preferred_provider=self._get_audit_provider(),
            model=self._get_audit_model(),
            temperature=0,
            max_tokens=3200,
            timeout=int(self.config.timeout),
        )
        parsed = self._extract_json_array(self._response_content_text(response)) or []
        normalized: List[Dict[str, Any]] = []
        seen_keys = set()
        for item in parsed:
            if not isinstance(item, dict):
                continue
            issue_type = str(item.get("problem_type") or item.get("type") or "规范性").strip() or "规范性"
            quote = str(item.get("quote") or "").strip()
            original = str(item.get("original") or quote or "").strip()
            suggestion = str(item.get("suggestion") or "").strip()
            context = str(item.get("context") or quote or original).strip()
            if not original and not context:
                continue
            confidence_value = self._normalize_confidence(item.get("confidence"))
            if confidence_value is None:
                # Keep schema strict to prevent low-quality payloads from polluting findings.
                continue
            if confidence_value < MIN_ISSUE_CONFIDENCE:
                continue
            if confidence_value > 1:
                confidence_value = 1.0
            if confidence_value < 0:
                confidence_value = 0.0
            if self._should_drop_rounding_issue(item, issue_type, original, suggestion, context):
                continue
            if self._should_drop_unverified_repeat_issue(
                section_text, issue_type, original, context
            ):
                continue

            span = item.get("span")
            if (
                not isinstance(span, list)
                or len(span) != 2
                or not all(isinstance(v, (int, float)) for v in span)
            ):
                anchor = original or context
                if anchor:
                    start = section_text.find(anchor)
                    if start >= 0:
                        span = [start, start + len(anchor)]
                    else:
                        span = [0, 0]
                else:
                    span = [0, 0]

            span_start = int(span[0])
            span_end = int(span[1])
            if span_start < 0:
                span_start = 0
            if span_end < span_start:
                span_end = span_start
            if section_text:
                max_len = len(section_text)
                span_start = min(span_start, max_len)
                span_end = min(span_end, max_len)

            severity = self._normalize_severity(item.get("severity"))
            page = self._normalize_page(item.get("page"))
            dedupe_key = (
                issue_type,
                severity,
                re.sub(r"\s+", "", original)[:100],
                span_start // 20,
                page or 0,
            )
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)

            normalized.append(
                {
                    "type": issue_type,
                    "original": original,
                    "suggestion": suggestion,
                    "span": [span_start, span_end],
                    "context": context,
                    "rule_id": item.get("rule_id"),
                    "title": item.get("title"),
                    "message": item.get("message"),
                    "severity": severity,
                    "problem_type": item.get("problem_type"),
                    "quote": quote or original,
                    "confidence": confidence_value,
                    "page": page,
                    "manual_confirm": bool(item.get("manual_confirm", False)),
                }
            )
        return normalized
        
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
                raise Exception(f"AI抽取器返回错误状态码: {response.status_code}, 响应: {response.text}")
                
            result = response.json()
            
            if "hits" not in result:
                raise Exception(f"AI抽取器返回格式错误: {result}")
                
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
                if not all(field in hit for field in required_fields):
                    logger.warning(f"跳过缺少必需字段的hit: {hit}")
                    continue
                    
                # 验证span格式
                for span_field in ["budget_span", "final_span", "stmt_span"]:
                    span = hit[span_field]
                    if not isinstance(span, list) or len(span) != 2:
                        logger.warning(f"跳过span格式错误的hit: {hit}")
                        continue
                        
                # 处理可选的reason_span
                reason_span = hit.get("reason_span")
                if reason_span and (not isinstance(reason_span, list) or len(reason_span) != 2):
                    logger.warning(f"reason_span格式错误，设为None: {reason_span}")
                    hit["reason_span"] = None
                    hit["reason_text"] = None
                
                converted.append(hit)
                
            except Exception as e:
                logger.warning(f"转换hit失败: {e}, hit: {hit}")
                continue
                
        return converted
    
    async def ai_semantic_audit(self, section_text: str, doc_hash: str) -> List[Dict[str, Any]]:
        """
        调用AI抽取器进行语义审计（错别字、重复、表达不当）
        
        Args:
            section_text: 待检查的文本内容
            doc_hash: 文档哈希
            
        Returns:
            语义问题列表，每个元素包含：
            - type: 错误类型（错别字/重复/表达不当/规范性）
            - original: 原文错误内容
            - suggestion: 修改建议
            - span: [start, end) 位置
            - context: 上下文片段
        """
        if not self.config.enabled:
            logger.debug("AI辅助未启用，返回空列表")
            return []
            
        if not section_text.strip():
            logger.debug("输入文本为空，返回空列表")
            return []
            
        try:
            result = await self._call_semantic_audit(section_text, doc_hash)
            if result:
                return result
            if self.config.direct_fallback:
                try:
                    logger.warning("Semantic audit service returned empty hits, falling back to direct LLM semantic audit")
                    return await self._direct_semantic_audit(section_text)
                except Exception as direct_err:
                    logger.error(f"Direct semantic fallback on empty hits failed: {direct_err}")
            return result
            
        except Exception as e:
            logger.error(f"AI语义审计失败: {e}")
            if self.config.direct_fallback:
                try:
                    logger.warning("Falling back to direct LLM semantic audit")
                    return await self._direct_semantic_audit(section_text)
                except Exception as direct_err:
                    logger.error(f"Direct semantic fallback failed: {direct_err}")
            return []

    async def ai_full_report_audit(self, section_text: str, doc_hash: str) -> List[Dict[str, Any]]:
        """
        全量报告审查：优先走直连大模型（Gemini等）以使用增强提示词，
        当直连失败时再回退到抽取服务语义审计。
        """
        if not self.config.enabled:
            logger.debug("AI辅助未启用，返回空列表")
            return []

        if not section_text.strip():
            logger.debug("输入文本为空，返回空列表")
            return []

        # 先走直连模型，确保使用全量审查提示词
        try:
            direct_result = await self._direct_semantic_audit(section_text)
            if direct_result:
                return direct_result
            logger.info("Direct full-report audit returned no issues, falling back to extractor semantic audit")
        except Exception as direct_err:
            logger.warning(f"Direct full-report audit failed: {direct_err}")

        # 直连失败或直连无结果时，再尝试抽取服务
        try:
            return await self.ai_semantic_audit(section_text, doc_hash)
        except Exception as e:
            logger.error(f"AI全量审查失败: {e}")
            return []
    
    async def _call_semantic_audit(self, section_text: str, doc_hash: str) -> List[Dict[str, Any]]:
        """语义审计的单次调用"""
        request_data = {
            "task": "semantic_audit_v1",
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
                raise Exception(f"AI语义审计返回错误状态码: {response.status_code}, 响应: {response.text}")
                
            result = response.json()
            
            if "hits" not in result:
                raise Exception(f"AI语义审计返回格式错误: {result}")
                
            hits = result["hits"]
            logger.info(f"AI语义审计成功，获得{len(hits)}个结果")
            
            # 提取语义问题
            semantic_issues = []
            for hit in hits:
                if "semantic_issues" in hit and hit["semantic_issues"]:
                    semantic_issues.extend(hit["semantic_issues"])
            
            return semantic_issues
    
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
