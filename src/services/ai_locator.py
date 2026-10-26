"""
AI 定位服务（辅助定位支路）。

只做一件事：为已存在的规则 finding 找一个可核验的 PDF 物理页码和原文片段。
定位不是定性：AI 候选必须在本页原文中逐字命中才接受；找不到就如实标记
不可用/未核实，不能把“无文本”或“模型猜的页码”当成定位成功。
"""
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from src.schemas.issues import JobContext, IssueItem
from src.services.ai_client import AIClient

logger = logging.getLogger(__name__)

_MAX_DOC_CHARS = 12000
_MAX_FINDINGS = 8


@dataclass
class LocationCandidate:
    """定位候选结果"""
    page: int
    text: str
    score: float
    bbox: Optional[List[float]] = None
    page_corrected: bool = False


@dataclass
class LocatorOutcome:
    status: str = "not_run"
    reason: Optional[str] = None
    text_source: Optional[str] = None
    document_chars: int = 0
    input_truncated: bool = False
    provider: Optional[str] = None
    model: Optional[str] = None
    finish_reason: Optional[str] = None
    token_usage: Optional[Dict[str, Any]] = None
    raw_count: int = 0
    accepted_count: int = 0
    rejected_count: int = 0
    rejection_reasons: Dict[str, int] = field(default_factory=dict)
    response_sha256: Optional[str] = None
    calls: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "text_source": self.text_source,
            "document_chars": self.document_chars,
            "input_truncated": self.input_truncated,
            "provider": self.provider,
            "model": self.model,
            "finish_reason": self.finish_reason,
            "token_usage": dict(self.token_usage or {}),
            "raw_count": self.raw_count,
            "accepted_count": self.accepted_count,
            "rejected_count": self.rejected_count,
            "rejection_reasons": dict(self.rejection_reasons),
            "response_sha256": self.response_sha256,
            "calls": [dict(call) for call in self.calls],
        }


class AILocator:
    """AI 定位器"""

    def __init__(self, client=None, model=None, provider=None):
        self.ai_client = client or AIClient()
        self.locator_model = model or os.getenv("AI_LOCATOR_MODEL", "").strip() or None
        self.locator_provider = provider or os.getenv("AI_LOCATOR_PROVIDER", "").strip() or None
        self.call_ledger: List[Dict[str, Any]] = []
        self.last_outcome = LocatorOutcome()

    async def enhance_finding(
        self, job_context: JobContext, finding: IssueItem
    ) -> IssueItem:
        self.last_outcome = LocatorOutcome()
        doc_text, text_source, truncated = self._document_text(job_context)
        if not doc_text.strip():
            self.last_outcome.status = "unavailable_no_text"
            self.last_outcome.reason = "no_page_texts_or_ocr_text"
            return self._annotate(
                finding,
                {"status": "unavailable_no_text", "reason": "no_page_texts_or_ocr_text"},
            )

        prompt = self._generate_locator_prompt(finding, doc_text, text_source)
        self.last_outcome.text_source = text_source
        self.last_outcome.document_chars = len(doc_text)
        self.last_outcome.input_truncated = truncated
        try:
            response = await self.ai_client.chat(
                messages=[
                    {"role": "system", "content": self._get_locator_system_prompt()},
                    {"role": "user", "content": prompt},
                ],
                model=self.locator_model,
                preferred_provider=self.locator_provider,
                temperature=0.1,
                max_tokens=1000,
            )
        except Exception as exc:
            error_type = type(exc).__name__
            self.last_outcome.status = "call_failed"
            self.last_outcome.reason = error_type
            self._record_call(
                provider=None,
                model=self.locator_model,
                content="",
                finish_reason=None,
                token_usage=None,
                error=error_type,
            )
            return self._annotate(finding, {"status": "call_failed", "reason": error_type})

        content = str(response.get("content") or "") if isinstance(response, dict) else str(response or "")
        provider = response.get("provider_used") if isinstance(response, dict) else None
        model = response.get("model") if isinstance(response, dict) else None
        finish_reason = response.get("finish_reason") if isinstance(response, dict) else None
        token_usage = response.get("tokens") if isinstance(response, dict) else None
        self.last_outcome.provider = provider
        self.last_outcome.model = model
        self.last_outcome.finish_reason = finish_reason
        self.last_outcome.token_usage = token_usage if isinstance(token_usage, dict) else None
        self.last_outcome.response_sha256 = _sha256(content)
        self._record_call(
            provider=provider,
            model=model,
            content=content,
            finish_reason=finish_reason if isinstance(finish_reason, str) else None,
            token_usage=token_usage if isinstance(token_usage, dict) else None,
            error=None,
        )
        if str(finish_reason or "").lower() in {"length", "max_tokens", "incomplete"}:
            self.last_outcome.status = "truncated"
            self.last_outcome.reason = "locator_response_truncated"
            return self._annotate(finding, {"status": "truncated", "reason": "locator_response_truncated"})

        candidates = self._parse_location_response(content)
        self.last_outcome.raw_count = len(candidates)
        verified = self._verify_candidates(candidates, job_context)
        self.last_outcome.accepted_count = len(verified)
        self.last_outcome.rejected_count = len(candidates) - len(verified)
        if not verified:
            self.last_outcome.status = "unverified"
            self.last_outcome.reason = "candidate_text_not_on_claimed_page"
            return self._annotate(
                finding,
                {"status": "unverified", "reason": "candidate_text_not_on_claimed_page"},
            )

        best = verified[0]
        self.last_outcome.status = "ok"
        enhanced = (
            finding.model_copy(deep=True)
            if hasattr(finding, "model_copy")
            else finding.copy()
        )
        locator_evidence = {
            "type": "text_content",
            "text": best.text,
            "text_snippet": best.text,
            "original": best.text,
            "context": best.text,
            "quote": best.text,
            "page": best.page,
            "source": "ai_locator",
            "location_level": "region",
            "verification_status": "unverified",
            "ai_locator_page_verified": True,
        }
        # 保留原证据，不覆盖规则命中的原文；把定位结果作为追加证据。
        enhanced.evidence = [*(finding.evidence or []), locator_evidence]
        enhanced.page_number = best.page
        loc = dict(enhanced.location or {})
        loc["page"] = best.page
        loc["ai_locator"] = {
            "status": "ok",
            "page": best.page,
            "page_corrected": best.page_corrected,
            "score": best.score,
            "text_source": text_source,
        }
        enhanced.location = loc
        enhanced.why_not = f"AI_LOCATED: score={best.score:.2f}"
        return enhanced

    def _annotate(self, finding: IssueItem, status: Dict[str, Any]) -> IssueItem:
        enhanced = (
            finding.model_copy(deep=True)
            if hasattr(finding, "model_copy")
            else finding.copy()
        )
        loc = dict(enhanced.location or {})
        loc["ai_locator"] = dict(status)
        enhanced.location = loc
        marker = f"AI_LOCATOR_UNVERIFIED: {status.get('reason')}"
        if enhanced.why_not and "AI_LOCATOR_UNVERIFIED" not in enhanced.why_not:
            enhanced.why_not = f"{enhanced.why_not}; {marker}"
        elif not enhanced.why_not:
            enhanced.why_not = marker
        return enhanced

    def _document_text(self, job_context: JobContext) -> tuple[str, str, bool]:
        pages = list(getattr(job_context, "page_texts", None) or [])
        if pages:
            parts = [
                f"【PDF 第 {index} 页】\n{text or ''}"
                for index, text in enumerate(pages, start=1)
            ]
            text = "\n\n".join(parts)
            source = "page_texts"
        else:
            text = str(getattr(job_context, "ocr_text", "") or "")
            source = "ocr_text" if text.strip() else "none"
        truncated = len(text) > _MAX_DOC_CHARS
        return text[:_MAX_DOC_CHARS], source, truncated

    def _generate_locator_prompt(
        self, finding: IssueItem, doc_content: str, text_source: str
    ) -> str:
        return f"""
请在以下文档中定位与问题相关的文本位置。文档文本来源：{text_source}。
页码是 PDF 物理页码（第一张页面为 1），不得使用页脚印刷页码、目录页码。

## 问题信息
- 规则ID: {finding.rule_id}
- 问题标题: {finding.title}
- 问题描述: {finding.message}
- 失败原因: {finding.why_not}

## 文档内容（带 PDF 物理页码标记）
{doc_content}

## 任务要求
请找出文档中与该问题最相关的文本片段，按相关性排序返回前3个候选位置。
text 必须从对应页面逐字复制；如果找不到，返回空数组 []。

输出格式（JSON数组）:
[
  {{"page": 1, "text": "相关文本摘录", "score": 0.95}},
  {{"page": 2, "text": "另一个相关文本", "score": 0.80}}
]

如果找不到相关内容，返回空数组 []。
"""

    def _get_locator_system_prompt(self) -> str:
        return """你是一个专业的文档定位助手。你的任务是在文档中找到与给定问题最相关的文本位置。

要求：
1. 仔细分析问题描述和失败原因
2. 在文档中寻找相关的关键词、数字、表格标题等
3. 按相关性评分（0-1之间）
4. 只返回有效的JSON格式
5. text字段必须是文档中的原文逐字摘录
6. page 必须是 PDF 物理页码；无法确定时不要输出该候选"""

    def _parse_location_response(self, content: str) -> List[LocationCandidate]:
        import json

        data: Any = None
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", content or "", re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(1))
                except json.JSONDecodeError:
                    data = None
            if data is None:
                match = re.search(r"(\[.*\])", content or "", re.DOTALL)
                if match:
                    try:
                        data = json.loads(match.group(1))
                    except json.JSONDecodeError:
                        data = None
        if not isinstance(data, list):
            return []
        candidates: List[LocationCandidate] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                page = int(item.get("page"))
                score = float(item.get("score"))
                text = str(item.get("text") or "").strip()
            except (TypeError, ValueError):
                continue
            if page <= 0 or not text or not 0 <= score <= 1:
                continue
            candidates.append(LocationCandidate(page=page, text=text, score=score))
        candidates.sort(key=lambda item: item.score, reverse=True)
        return candidates

    def _verify_candidates(
        self, candidates: Sequence[LocationCandidate], job_context: JobContext
    ) -> List[LocationCandidate]:
        pages = list(getattr(job_context, "page_texts", None) or [])
        if not pages:
            return []
        verified: List[LocationCandidate] = []
        for candidate in candidates:
            page_text = pages[candidate.page - 1] if 1 <= candidate.page <= len(pages) else ""
            if _quote_on_page(candidate.text, page_text):
                verified.append(candidate)
                continue
            # 模型页码可能错：逐页核对原文，找到唯一页时纠正并如实标记。
            matches = [
                index + 1
                for index, text in enumerate(pages)
                if _quote_on_page(candidate.text, text)
            ]
            if len(matches) == 1:
                candidate.page = matches[0]
                candidate.page_corrected = True
                verified.append(candidate)
        return verified

    def _record_call(
        self,
        *,
        provider: Optional[str],
        model: Optional[str],
        content: str,
        finish_reason: Optional[str],
        token_usage: Optional[Dict[str, Any]],
        error: Optional[str],
    ) -> None:
        self.call_ledger.append(
            {
                "provider": provider,
                "model": model,
                "finish_reason": finish_reason,
                "token_usage": token_usage,
                "content": content or "",
                "content_length": len(content or ""),
                "error": error,
                "timestamp": time.time(),
            }
        )
        self.last_outcome.calls.append(
            {
                "provider": provider,
                "model": model,
                "finish_reason": finish_reason,
                "token_usage": token_usage,
                "content_length": len(content or ""),
                "error": error,
            }
        )

    def pop_call_ledger(self) -> List[Dict[str, Any]]:
        ledger, self.call_ledger = self.call_ledger, []
        return ledger


def _quote_on_page(quote: str, page_text: str) -> bool:
    target = re.sub(r"\s+", "", str(quote or ""))
    body = re.sub(r"\s+", "", str(page_text or ""))
    return bool(target) and target in body


def _sha256(text: str) -> str:
    import hashlib

    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


async def locate_with_ai(job_context: JobContext, finding: IssueItem) -> IssueItem:
    """便捷的 AI 定位函数"""
    locator = AILocator()
    return await locator.enhance_finding(job_context, finding)


__all__ = ["AILocator", "LocationCandidate", "LocatorOutcome", "locate_with_ai"]
