"""
AI检查服务
直接调用 LLM 进行合规检查，生成统一的 IssueItem 格式结果
"""
import logging
import json
import time
import asyncio
import os
from typing import List, Dict, Any, Mapping, Optional
import traceback
import re

from src.schemas.issues import IssueItem, JobContext, AnalysisConfig
from src.engine.ai.extractor_client import ExtractorClient, ExtractorConfig
from src.services.check_progress import update_progress
from src.services.ai_issue_interpreter import interpret_ai_issue, normalize_ai_severity
from src.services.ai_input_builder import build_structured_context
from src.services.ai_candidate_contract import validate_ai_candidate_batch
from src.utils.logging_config import describe_exception, safe_log_extra
from src.utils.provenance import ENGINE_VERSION, read_finding_provenance

logger = logging.getLogger(__name__)


class AIFindingsService:
    """AI检查服务"""
    
    def __init__(self, config: AnalysisConfig, runtime_config: Optional[dict] = None):
        self.config = config
        self.ai_client = ExtractorClient()  # 复用现有AI客户端
        if runtime_config:
            from src.services.ai_model_settings import create_client
            self.ai_client = ExtractorClient(ExtractorConfig(
                enabled=True, direct_only=True, audit_provider="managed", main_provider="managed",
                audit_model=runtime_config["model"], main_model=runtime_config["model"],
                timeout=runtime_config["timeout_s"], max_tokens=runtime_config["max_tokens"],
            ))
            self.ai_client._direct_ai_client = create_client(runtime_config)
        self.ai_errors = []  # 聚合AI错误信息
        self.audit_window_chars = self._read_int_env("AI_AUDIT_WINDOW_CHARS", 12000, 2000)
        self.audit_overlap_chars = self._read_int_env("AI_AUDIT_WINDOW_OVERLAP", 1200, 200)
        if self.audit_overlap_chars >= self.audit_window_chars:
            self.audit_overlap_chars = max(200, self.audit_window_chars // 4)
        self.audit_max_windows = self._read_int_env("AI_AUDIT_MAX_WINDOWS", 12, 1)
        self.audit_max_concurrency = self._read_int_env("AI_AUDIT_MAX_CONCURRENCY", 2, 1)
    
    async def analyze(self, context: JobContext) -> List[IssueItem]:
        """执行AI分析"""
        # 确保配置项有合理的默认值
        ai_enabled = getattr(self.config, 'ai_enabled', True)
        if not ai_enabled:
            logger.info("AI分析已禁用")
            return []
        
        start_time = time.time()
        self._progress_job_id = context.job_id
        logger.info(f"开始AI分析: job_id={context.job_id}")

        # 重置错误计数
        self.ai_errors = []

        # 防御性留痕清理（review 🟢2）：ledger 里的 content 含材料原文，
        # 正常链路由 analyze_dual 在任务结束时 pop 走；若上一任务异常路径
        # 漏 pop，残留留痕会跨任务累积且可能把旧正文计入本次 ai_execution
        # 判定——入口处丢弃残留，保证每次分析的留痕只属于本任务。
        stale_ledger = self.ai_client.pop_call_ledger()
        if stale_ledger:
            logger.warning(
                "丢弃上一任务残留的 %d 条 AI 调用留痕（异常路径未 pop）",
                len(stale_ledger),
            )

        try:
            # 合并所有页面文本用于语义审计，同时计算页码偏移量
            page_texts = context.page_texts if context.page_texts else []
            if not page_texts:
                logger.warning("文档文本为空，跳过AI语义审计")
                return []
            
            # 计算每页的起始偏移量（用于后续页码映射）
            page_offsets = []  # [(start_offset, end_offset, page_number), ...]
            current_offset = 0
            for i, text in enumerate(page_texts):
                page_start = current_offset
                page_end = current_offset + len(text)
                page_offsets.append((page_start, page_end, i + 1))  # 页码从1开始
                current_offset = page_end + 1  # +1 for \n separator
            
            all_text = "\n".join(page_texts)
            self.page_offsets = page_offsets
            if not all_text.strip():
                logger.warning("文档文本为空，跳过AI语义审计")
                return []
            
            # 生成文档哈希
            import hashlib
            doc_hash = hashlib.sha1(all_text[:5000].encode('utf-8')).hexdigest()[:12]

            # 分窗执行AI审计，避免15000字符硬截断导致漏检
            semantic_issues: List[Dict[str, Any]] = []
            windows = self._build_text_windows(all_text)
            # 输入表征增强（计划 §4）：一次性构建「结构化事实 + 表格关系 +
            # 页码」上下文，随每个窗口注入 prompt；模型只做语义候选，
            # 金额勾稽以确定性规则为准
            self.structured_context = build_structured_context(
                page_texts,
                getattr(context, "page_tables", None),
            )
            if windows and (windows[-1][0] + len(windows[-1][1]) < len(all_text)):
                logger.warning(
                    "AI审计窗口上限触发，文档后半部分未进入AI审计: covered=%s, total=%s",
                    windows[-1][0] + len(windows[-1][1]),
                    len(all_text),
                )
                self.ai_errors.append(
                    {
                        "type": "audit_window_limit_reached",
                        "covered_chars": windows[-1][0] + len(windows[-1][1]),
                        "total_chars": len(all_text),
                        "windows": len(windows),
                        "window_size": self.audit_window_chars,
                        "max_windows": self.audit_max_windows,
                        "timestamp": time.time(),
                    }
                )
            window_batches = await self._run_window_audits(
                windows=windows,
                doc_hash=doc_hash,
                total_text_len=len(all_text),
            )
            for window_issues in window_batches:
                semantic_issues.extend(window_issues)
            semantic_issues = self._dedupe_semantic_issues(semantic_issues)
            
            # 转换为IssueItem格式，传入页码偏移量
            issues = self._convert_semantic_issues_to_items(semantic_issues, context, page_offsets)
            issues = self._populate_bbox_hints(issues, context)
            
            elapsed = time.time() - start_time
            logger.info(f"AI分析完成: job_id={context.job_id}, issues={len(issues)}, elapsed={elapsed:.2f}s")
            
            return issues
            
        except Exception as e:
            # 用 logger.exception：异常栈进 StructuredFormatter 的独立 exception 字段，
            # message 保持无原文（把 traceback 拼进 message 会带出 pydantic 校验异常里的输入值）
            logger.exception(
                "AI分析失败",
                extra=safe_log_extra({"job_id": context.job_id, **describe_exception(e)}),
            )
            
            # 记录分析失败错误
            self.ai_errors.append({
                "type": "analysis_failure",
                "message": str(e),
                "timestamp": time.time()
            })
            
            return []
    
    def _convert_semantic_issues_to_items(self, semantic_issues: List[Dict[str, Any]], context: JobContext, page_offsets: List[tuple] = None) -> List[IssueItem]:
        """将语义问题转换为IssueItem格式"""
        issues = []
        page_ranges = page_offsets or []
        known_page_count = (
            len(page_offsets)
            if page_offsets is not None
            else len(context.page_texts or [])
        )
        
        for idx, issue in enumerate(semantic_issues):
            try:
                confidence = issue.get("confidence")
                try:
                    if confidence is not None and float(confidence) < 0.75:
                        continue
                except Exception:
                    pass

                # 只有明确的页码或能映射到原始页文本的 span 才携带页码。
                # 缺少 span 时不把默认偏移 0 误当第一页。
                page_number: Optional[int] = None
                try:
                    raw_page = int(str(issue.get("page")).strip())
                    if 0 < raw_page and (known_page_count == 0 or raw_page <= known_page_count):
                        page_number = raw_page
                except (TypeError, ValueError):
                    pass

                span = issue.get("span")
                span_start: Optional[int] = None
                full_text = "\n".join(context.page_texts or [])
                anchor = str(issue.get("quote") or issue.get("original") or "")
                if isinstance(span, (list, tuple)) and len(span) == 2:
                    try:
                        candidate_start = int(span[0])
                        # [0, 0] 是提取器的未命中占位符，不能映射成第 1 页。
                        candidate_end = int(span[1])
                        actual_span = full_text[candidate_start:candidate_end]
                        if (0 <= candidate_start < candidate_end <= len(full_text)
                                and anchor.strip()
                                and re.sub(r"\s+", "", actual_span) == re.sub(r"\s+", "", anchor)):
                            span_start = candidate_start
                    except (TypeError, ValueError):
                        pass
                if span_start is not None:
                    for page_start, page_end, page_num in page_ranges:
                        if page_start <= span_start < page_end:
                            page_number = page_num
                            break

                item = self._build_ai_issue_item(
                    raw_issue=issue,
                    context=context,
                    idx=idx,
                    page_number=page_number,
                )
                issues.append(item)
                
            except Exception as e:
                # 只记可定位信息（序号 / 规则号 / 字段名）+ 错误指纹。
                # 绝不能把 issue 整体拼进 message：它带着 evidence_text 等 PDF 原文，
                # 而 message 字段不经过 redact_log_fields 脱敏。
                logger.warning(
                    "转换语义问题失败",
                    extra=safe_log_extra(
                        {
                            "issue_index": idx,
                            "issue_rule_id": str(issue.get("rule_id") or "")
                            if isinstance(issue, dict)
                            else "",
                            "issue_field_names": sorted(str(key) for key in issue)
                            if isinstance(issue, dict)
                            else [],
                            **describe_exception(e),
                        }
                    ),
                )
                continue
        
        return issues

    def _build_ai_issue_item(
        self,
        *,
        raw_issue: Dict[str, Any],
        context: JobContext,
        idx: int,
        page_number: Optional[int],
    ) -> IssueItem:
        fallback_rule_id = f"AI-SEM-{idx:03d}"
        interpreted = interpret_ai_issue(
            raw_issue,
            page_number=page_number,
            fallback_rule_id=fallback_rule_id,
        )

        evidence_text = str(
            interpreted["quote"] or interpreted["context"] or interpreted["original"] or ""
        )
        evidence_item: Dict[str, Any] = {
            "type": "text_content",
            "text": evidence_text,
            "text_snippet": evidence_text,
            "original": interpreted["original"],
            "context": interpreted["context"],
            "quote": interpreted["quote"],
        }
        if page_number is not None:
            evidence_item["page"] = page_number
        evidence = [evidence_item]

        raw_bbox = raw_issue.get("bbox")
        location_level = str(raw_issue.get("location_level") or "unresolved")
        verification_status = str(raw_issue.get("verification_status") or "unverified")
        if (
            isinstance(raw_bbox, list)
            and len(raw_bbox) == 4
            and location_level in {"exact_cell", "exact_span"}
            and verification_status == "verified"
        ):
            evidence[0]["bbox"] = raw_bbox
            evidence[0]["location_level"] = location_level
            evidence[0]["verification_status"] = verification_status

        # 版本留痕（P2-02）：AI finding 记录实际使用的模型与提示词版本。
        # 这里不填 rule_version——本条问题是语义审计提示词产出的，
        # 并非本地规则集里某条规则命中的结果，硬填规则版本会造成误导性归因。
        provenance = read_finding_provenance(raw_issue)

        # AC-11/RC-11：候选声明的身份必须过白名单——fact/evidence ID 不在
        # 当前事实上下文里的（模型幻觉 ID）一律剔除并留计数，不透传给合并器；
        # 模型声称的 expected/actual 金额只是"模型声称"，不是系统计算结果，
        # 打上 provenance 标记供界面与导出区分。
        candidate_relation_id = str(raw_issue.get("check_relation_id") or "").strip() or None
        raw_fact_ids = raw_issue.get("fact_ids", [])
        raw_evidence_ids = raw_issue.get("evidence_ids", [])
        fact_context = getattr(context, "fact_context", None)
        filtered_metrics = dict(interpreted["metrics"])
        if isinstance(raw_issue.get("page"), int) and raw_issue["page"] > 0:
            filtered_metrics["ai_reported_page"] = raw_issue["page"]
        fact_context_data = (
            fact_context if isinstance(fact_context, Mapping) else {}
        )
        raw_facts = fact_context_data.get("facts")
        raw_facts = raw_facts if isinstance(raw_facts, list) else []
        known_fact_ids: set[str] = set()
        known_evidence_ids: set[str] = set()
        known_relation_ids: set[str] = set()
        evidence_ids_by_fact: Dict[str, set[str]] = {}
        for fact in raw_facts:
            if not isinstance(fact, Mapping):
                continue
            fact_uid = str(fact.get("fact_uid") or "").strip()
            if fact_uid:
                known_fact_ids.add(fact_uid)
                evidence_ids_by_fact.setdefault(fact_uid, set())
            evidence_ids = fact.get("evidence_ids")
            if isinstance(evidence_ids, list):
                normalized_evidence = {
                    str(item).strip() for item in evidence_ids if str(item).strip()
                }
                known_evidence_ids.update(normalized_evidence)
                if fact_uid:
                    evidence_ids_by_fact[fact_uid].update(normalized_evidence)
            extra_dims = fact.get("extra_dims") if isinstance(fact.get("extra_dims"), Mapping) else {}
            refs = extra_dims.get("evidence_refs")
            if isinstance(refs, list):
                ref_ids = {
                    str(ref.get("evidence_id") or "").strip()
                    for ref in refs
                    if isinstance(ref, Mapping) and str(ref.get("evidence_id") or "").strip()
                }
                known_evidence_ids.update(ref_ids)
                if fact_uid:
                    evidence_ids_by_fact[fact_uid].update(ref_ids)
        for source in (fact_context_data, getattr(context, "meta", {})):
            source = source if isinstance(source, Mapping) else {}
            receipts = source.get("check_receipts") or source.get("required_checks")
            if not isinstance(receipts, list):
                continue
            for item in receipts:
                if isinstance(item, Mapping):
                    relation = str(item.get("check_relation_id") or "").strip()
                    if not relation and item.get("rule_id") and item.get("sub_check_id"):
                        relation = f"{item['rule_id']}:{item['sub_check_id']}"
                    if relation:
                        known_relation_ids.add(relation)
                elif isinstance(item, str) and item.strip():
                    known_relation_ids.add(item.strip())

        candidate_id = str(raw_issue.get("candidate_id") or f"{context.job_id}:semantic:{idx}").strip()
        contract_candidate: Dict[str, Any] = {
            "candidate_id": candidate_id,
            "fact_ids": raw_fact_ids,
            "evidence_ids": raw_evidence_ids,
            "fact_candidates": raw_issue.get("fact_candidates", []),
            "semantic_uncertainties": raw_issue.get("semantic_uncertainties", []),
        }
        if "check_relation_id" in raw_issue:
            contract_candidate["check_relation_id"] = raw_issue.get("check_relation_id")
        contract = validate_ai_candidate_batch(
            {"candidates": [contract_candidate]},
            allowed_fact_ids=known_fact_ids,
            allowed_evidence_ids=known_evidence_ids,
            allowed_relation_ids=known_relation_ids,
        )
        accepted_candidates = contract.get("accepted") or []
        contract_accepted = bool(accepted_candidates)
        validated = accepted_candidates[0] if contract_accepted else {}
        candidate_relation_id = None
        rejection_reasons: List[str] = []
        if contract_accepted:
            candidate_fact_ids = list(validated.get("fact_ids") or [])
            candidate_evidence_ids = list(validated.get("evidence_ids") or [])
            candidate_relation_id = validated.get("check_relation_id")
            if candidate_relation_id and candidate_fact_ids:
                submitted_evidence = set(candidate_evidence_ids)
                unanchored_facts = [
                    fact_id for fact_id in candidate_fact_ids
                    if not (submitted_evidence & evidence_ids_by_fact.get(fact_id, set()))
                ]
                if not candidate_evidence_ids or unanchored_facts:
                    contract_accepted = False
                    rejection_reasons.append("fact_evidence_mismatch")
            if contract_accepted and candidate_relation_id and candidate_fact_ids:
                linked_evidence = set().union(
                    *(evidence_ids_by_fact.get(fact_id, set()) for fact_id in candidate_fact_ids)
                )
                for fact_candidate in validated.get("fact_candidates") or []:
                    candidate_fact_evidence = set(fact_candidate.get("evidence_ids") or [])
                    if (
                        not candidate_fact_evidence
                        or not candidate_fact_evidence.issubset(linked_evidence)
                        or not (candidate_fact_evidence & submitted_evidence)
                    ):
                        contract_accepted = False
                        rejection_reasons.append("candidate_value_evidence_mismatch")
                        break

        def _safe_candidate_ids(value: Any, allowed: set[str]) -> List[str]:
            if not isinstance(value, list):
                return []
            return list(dict.fromkeys(
                str(item).strip() for item in value
                if str(item).strip() and str(item).strip() in allowed
            ))

        filter_note: Dict[str, int] = {}
        if contract_accepted:
            contract_state = "accepted"
        else:
            candidate_fact_ids = _safe_candidate_ids(raw_fact_ids, known_fact_ids)
            candidate_evidence_ids = _safe_candidate_ids(raw_evidence_ids, known_evidence_ids)
            if isinstance(raw_fact_ids, list):
                filter_note["unknown_fact_ids"] = len(raw_fact_ids) - len(candidate_fact_ids)
            elif raw_fact_ids not in (None, []):
                filter_note["invalid_fact_ids_field"] = 1
            if isinstance(raw_evidence_ids, list):
                filter_note["unknown_evidence_ids"] = len(raw_evidence_ids) - len(candidate_evidence_ids)
            elif raw_evidence_ids not in (None, []):
                filter_note["invalid_evidence_ids_field"] = 1
            candidate_relation_id = raw_issue.get("check_relation_id")
            if candidate_relation_id and candidate_relation_id not in known_relation_ids:
                candidate_relation_id = None
                filter_note["unknown_relation_ids"] = 1
            rejected = (contract.get("rejected") or [{}])[0]
            if rejected.get("error") != "unknown_source_reference":
                filter_note["contract_rejections"] = 1
            if not rejection_reasons:
                raw_error = rejected.get("error")
                rejection_reasons.append(
                    raw_error if isinstance(raw_error, str) else "candidate_schema_invalid"
                )
            if raw_issue.get("fact_candidates"):
                filter_note["rejected_fact_candidates"] = len(raw_issue.get("fact_candidates")) if isinstance(raw_issue.get("fact_candidates"), list) else 1
            contract_state = "rejected"
        if filter_note:
            filtered_metrics["ai_candidate_ids_filtered"] = filter_note
        filtered_metrics["ai_candidate_id"] = candidate_id
        filtered_metrics["ai_candidate_contract"] = "accepted" if contract_accepted else "identity_claims_removed"
        filtered_metrics["ai_candidate_contract_state"] = contract_state
        filtered_metrics["ai_merge_eligible"] = contract_accepted
        if not contract_accepted:
            filtered_metrics["ai_candidate_rejection_reasons"] = list(dict.fromkeys(rejection_reasons))
        if contract_accepted and validated.get("semantic_uncertainties"):
            filtered_metrics["ai_semantic_uncertainties"] = validated["semantic_uncertainties"]
        if contract_accepted and validated.get("fact_candidates"):
            filtered_metrics["ai_fact_candidates"] = validated["fact_candidates"]
        if any(
            key in filtered_metrics for key in ("expected", "actual", "difference")
        ):
            filtered_metrics["amounts_provenance"] = "model_claimed_not_verified"

        return IssueItem(
            id=f"ai_semantic_{context.job_id}_{idx}_{int(time.time())}",
            source="ai",
            rule_id=str(interpreted["rule_id"]),
            severity=str(interpreted["severity"]),
            severity_label=str(interpreted.get("severity_label") or ""),
            title=str(interpreted["title"]),
            message=str(interpreted["message"]),
            evidence=evidence,
            location=dict(interpreted["location"]),
            metrics=dict(filtered_metrics),
            page_number=page_number,
            bbox=(
                raw_bbox
                if isinstance(raw_bbox, list)
                and len(raw_bbox) == 4
                and location_level in {"exact_cell", "exact_span"}
                and verification_status == "verified"
                else None
            ),
            location_level=location_level,
            verification_status=verification_status,
            suggestion=str(interpreted["suggestion"]),
            tags=list(
                dict.fromkeys(
                    [
                        *interpreted["tags"],
                        "AI检测",
                        "语义审计",
                        "全量审查",
                    ]
                )
            ),
            created_at=time.time(),
            check_relation_id=candidate_relation_id,
            fact_ids=candidate_fact_ids,
            evidence_ids=candidate_evidence_ids,
            model_version=provenance["model_version"],
            prompt_version=provenance["prompt_version"],
            engine_version=ENGINE_VERSION,
        )

    def _populate_bbox_hints(self, issues: List[IssueItem], context: JobContext) -> List[IssueItem]:
        if not issues or not getattr(context, "pdf_path", None):
            return issues

        from src.utils.issue_bbox import PDFBBoxLocator

        locator = PDFBBoxLocator(str(context.pdf_path))
        try:
            resolved: List[IssueItem] = []
            for issue in issues:
                try:
                    resolved.append(locator.locate(issue))
                except Exception as exc:
                    logger.warning("AI issue bbox locate failed: %s", exc)
                    resolved.append(issue)
            return resolved
        finally:
            locator.close()

    async def _run_window_audits(
        self,
        windows: List[tuple[int, str]],
        doc_hash: str,
        total_text_len: int,
    ) -> List[List[Dict[str, Any]]]:
        if not windows:
            return []

        semaphore = asyncio.Semaphore(self.audit_max_concurrency)
        completed = 0
        failed = 0
        job_id = getattr(self, "_progress_job_id", None)
        if job_id:
            update_progress(job_id, "ai", state="running", total=len(windows), completed=0, current_step="正在检查文本", unit="段文本")

        async def audit_one(window_idx: int, window_start: int, window_text: str) -> List[Dict[str, Any]]:
            nonlocal completed, failed
            import hashlib

            window_hash = hashlib.sha1(
                f"{doc_hash}:{window_idx}:{window_start}".encode("utf-8")
            ).hexdigest()[:12]
            async with semaphore:
                try:
                    window_issues = await self.ai_client.ai_full_report_audit(
                        window_text,
                        window_hash,
                        structured_context=self._window_page_context(window_start, len(window_text))
                            + (getattr(self, "structured_context", None) or ""),
                    )
                except asyncio.CancelledError:
                    failed += 1
                    self.ai_errors.append({"type": "audit_window_cancelled", "window_index": window_idx, "message": "检查超时或已取消", "timestamp": time.time()})
                    raise
                except Exception as exc:
                    failed += 1
                    logger.warning("AI window audit failed: window=%s error=%s", window_idx, exc)
                    self.ai_errors.append(
                        {
                            "type": "audit_window_failed",
                            "window_index": window_idx,
                            "window_start": window_start,
                            "message": str(exc),
                            "timestamp": time.time(),
                        }
                    )
                    return []
                finally:
                    completed += 1
                    if job_id:
                        update_progress(job_id, "ai", completed=completed, failed=failed, current_step=f"已处理 {completed}/{len(windows)} 段文本")

            for issue in window_issues:
                self._shift_issue_span(issue, window_start, total_text_len)
            return window_issues

        tasks = [
            asyncio.create_task(audit_one(window_idx, window_start, window_text))
            for window_idx, (window_start, window_text) in enumerate(windows)
        ]
        return await asyncio.gather(*tasks)

    def _window_page_context(self, start: int, length: int) -> str:
        lines = ["【当前原文窗口的 PDF 物理页码映射】",
                 "以下区间为当前窗口从 0 开始的字符偏移 [start,end)，页码从 PDF 第一张页面计为 1。",
                 "span 必须对应逐字引文；page 根据区间填写，不使用页脚、目录页码或窗口序号。无法确定填 null。"]
        for page_start, page_end, page in getattr(self, "page_offsets", []):
            lo, hi = max(start, page_start), min(start + length, page_end)
            if lo < hi:
                lines.append(f"[{lo - start},{hi - start}) = PDF 第 {page} 页")
        return "\n".join(lines) + "\n【页码映射结束】\n"

    @staticmethod
    def _read_int_env(name: str, default: int, min_value: int) -> int:
        try:
            value = int(os.getenv(name, str(default)))
            if value < min_value:
                return min_value
            return value
        except Exception:
            return default

    def _build_text_windows(self, full_text: str) -> List[tuple]:
        if not full_text:
            return []
        text_len = len(full_text)
        windows: List[tuple] = []
        start = 0
        while start < text_len and len(windows) < self.audit_max_windows:
            end = min(start + self.audit_window_chars, text_len)
            windows.append((start, full_text[start:end]))
            if end >= text_len:
                break
            start = max(0, end - self.audit_overlap_chars)
        return windows

    def _shift_issue_span(self, issue: Dict[str, Any], offset: int, total_len: int) -> None:
        if offset <= 0:
            return
        span = issue.get("span")
        if (
            not isinstance(span, list)
            or len(span) != 2
            or not all(isinstance(v, (int, float)) for v in span)
        ):
            return
        start = max(0, int(span[0]) + offset)
        end = max(start, int(span[1]) + offset)
        if total_len > 0:
            start = min(start, total_len)
            end = min(end, total_len)
        issue["span"] = [start, end]

    def _dedupe_semantic_issues(self, semantic_issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        unique: List[Dict[str, Any]] = []
        seen = set()
        for issue in semantic_issues:
            if not isinstance(issue, dict):
                continue
            span = issue.get("span", [0, 0])
            span_bucket = 0
            if isinstance(span, list) and len(span) == 2 and isinstance(span[0], (int, float)):
                span_bucket = int(span[0]) // 25
            key = (
                str(issue.get("rule_id") or ""),
                str(issue.get("type") or issue.get("problem_type") or ""),
                re.sub(r"\s+", "", str(issue.get("original") or issue.get("quote") or ""))[:80],
                re.sub(r"\s+", "", str(issue.get("suggestion") or ""))[:80],
                span_bucket,
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(issue)
        return unique
    
    def _build_prompt(self, context: JobContext) -> str:
        """构建AI提示词"""
        # 确保所有属性都有合理的默认值
        pages = getattr(context, 'pages', 0)
        ocr_text = getattr(context, 'ocr_text', '')
        tables = getattr(context, 'tables', [])
        
        prompt = f"""
你是一个专业的政府预决算合规检查专家。请对以下预决算文档进行全面的合规性和一致性检查。

## 文档信息
- 文件路径: {context.pdf_path}
- 页数: {pages}
- OCR文本长度: {len(ocr_text or '') // 1000}K字符

## 检查要求
请重点检查以下方面，发现问题时必须提供具体的页码和文本证据：

1. **九张表齐全性**: 检查是否包含完整的预决算表格
2. **目录-正文一致性**: 检查目录与正文内容是否对应
3. **表内勾稽关系**: 检查总表恒等式和明细汇总
4. **预算vs决算差异**: 检查预算执行情况的合理性
5. **比例和百分比**: 检查各项比例计算是否正确
6. **年份和页码**: 检查年份标注和页码连续性
7. **三公经费口径**: 检查三公经费统计口径一致性
8. **政府采购口径**: 检查政府采购统计规范性
9. **类款项编码**: 检查预算科目编码规范性

## OCR文本内容
{ocr_text[:10000] if ocr_text else "无OCR文本"}

## 表格数据
{json.dumps(tables[:5], ensure_ascii=False, indent=2) if tables else "无表格数据"}

## 输出格式要求
请严格按照以下JSON格式输出，每个问题必须包含所有必需字段：

```json
[
  {{"rule_id": "AI-XXX-001",
    "title": "问题标题",
    "message": "详细问题描述",
                "severity": "critical|high|medium|low|info|manual_review",
    "page": 页码数字,
    "section": "所在章节",
    "table": "所在表格名称",
    "evidence": "具体的文本证据摘录",
    "metrics": {{
      "expected": 期望值,
      "actual": 实际值,
      "diff": 差异值,
      "pct": 百分比
    }},
    "suggestion": "改进建议",
    "tags": ["标签1", "标签2"],
    "category": "问题分类"
  }}
]
```

## 重要约束
1. 必须基于实际文本内容，不得编造问题
2. 每个问题必须提供具体的页码和文本证据
3. 金额数据必须准确，不得估算
4. 严重程度要合理评估
5. 建议要具体可操作
6. 如果没有发现问题，返回空数组 []

请开始检查：
"""
        return prompt
    
    async def _call_ai_with_retry(self, prompt: str, context: JobContext) -> str:
        """带重试的AI调用"""
        last_error = None
        
        for attempt in range(self.config.ai_retry + 1):
            try:
                logger.info(f"AI调用尝试 {attempt + 1}/{self.config.ai_retry + 1}")
                
                # 使用现有的AI客户端
                response = await asyncio.wait_for(
                    self._call_ai_client(prompt),
                    timeout=self.config.ai_timeout
                )
                
                if response and response.strip():
                    return response
                else:
                    raise ValueError("AI返回空响应")
                    
            except asyncio.TimeoutError:
                last_error = f"AI调用超时 ({self.config.ai_timeout}s)"
                logger.warning(f"{last_error}, 尝试 {attempt + 1}")
            except Exception as e:
                last_error = f"AI调用失败: {e}"
                logger.warning(f"{last_error}, 尝试 {attempt + 1}")
            
            if attempt < self.config.ai_retry:
                await asyncio.sleep(2 ** attempt)  # 指数退避
        
        raise Exception(f"AI调用最终失败: {last_error}")
    
    async def _call_ai_client(self, prompt: str) -> str:
        """调用AI客户端"""
        try:
            # 注意：现有的ExtractorClient不支持chat_completion方法
            # 这里提供一个默认实现，返回模拟结果
            logger.warning("使用模拟AI响应，因为ExtractorClient不支持chat_completion方法")
            return self._get_mock_ai_response()
        except Exception as e:
            logger.error(f"AI客户端调用失败: {e}")
            raise
    
    # 移除同步AI调用方法，所有AI调用都应该使用异步方式
    # async def _sync_call_ai(self, prompt: str) -> str:
    #     """同步AI调用（兼容现有代码）"""
    #     try:
    #         # 这里需要根据实际的AI客户端接口调整
    #         # 返回模拟结果用于测试
    #         return self._get_mock_ai_response()
    #     except Exception as e:
    #         logger.error(f"同步AI调用失败: {e}")
    #         raise
    
    def _get_mock_ai_response(self) -> str:
        """获取模拟AI响应（用于测试）"""
        mock_response = [
            {
                "rule_id": "AI-COMP-001",
                "title": "预算收支总表缺失",
                "message": "未在文档中发现完整的预算收支总表，可能影响预算执行情况的全面了解",
                "severity": "high",
                "page": 1,
                "section": "预算表",
                "table": "预算收支总表",
                "evidence": "第1页目录显示应有预算收支总表，但正文中未找到对应表格",
                "metrics": {
                    "expected": 1,
                    "actual": 0,
                    "diff": 1
                },
                "suggestion": "请补充完整的预算收支总表，包含收入和支出的详细分类",
                "tags": ["表格完整性", "预算表"],
                "category": "表格缺失"
            },
            {
                "rule_id": "AI-CALC-001", 
                "title": "收入明细汇总不符",
                "message": "各项收入明细汇总与总收入金额不一致，存在计算错误",
                "severity": "medium",
                "page": 3,
                "section": "收入明细",
                "table": "收入明细表",
                "evidence": "第3页收入明细表显示：税收收入800万+非税收入150万=950万，但总收入显示1000万",
                "metrics": {
                    "expected": 10000000,
                    "actual": 9500000,
                    "diff": 500000,
                    "pct": 5.0
                },
                "suggestion": "请核对收入明细计算，确保各项明细汇总与总额一致",
                "tags": ["金额一致性", "收入"],
                "category": "计算错误"
            }
        ]
        return json.dumps(mock_response, ensure_ascii=False, indent=2)
    
    def _parse_ai_response(self, response: str, context: JobContext) -> List[IssueItem]:
        """解析AI响应"""
        discarded_count = 0
        discarded_examples = []
        
        try:
            # 提取JSON内容
            json_content = self._extract_json_from_response(response)
            if not json_content:
                logger.warning("AI响应中未找到有效JSON")
                self.ai_errors.append({
                    "type": "json_extraction_failed",
                    "message": "AI响应中未找到有效JSON",
                    "response_preview": response[:200] + "..." if len(response) > 200 else response
                })
                return []
            
            # 解析JSON
            raw_issues = json.loads(json_content)
            if not isinstance(raw_issues, list):
                logger.warning("AI响应JSON格式错误，应为数组")
                self.ai_errors.append({
                    "type": "json_format_error",
                    "message": "AI响应JSON格式错误，应为数组",
                    "actual_type": type(raw_issues).__name__
                })
                return []
            
            # 转换为IssueItem
            issues = []
            for idx, raw_issue in enumerate(raw_issues):
                try:
                    issue = self._convert_ai_issue(raw_issue, context, idx)
                    if issue:
                        issues.append(issue)
                    else:
                        discarded_count += 1
                        if len(discarded_examples) < 3:  # 只保存前3个示例
                            discarded_examples.append({
                                "index": idx,
                                "issue": raw_issue,
                                "reason": "conversion_failed"
                            })
                except Exception as e:
                    # 同上：raw_issue 里是 AI 回传的证据原文，只能记序号与错误指纹
                    logger.error(
                        "转换AI问题失败",
                        extra=safe_log_extra(
                            {
                                "issue_index": idx,
                                "issue_rule_id": str(raw_issue.get("rule_id") or "")
                                if isinstance(raw_issue, dict)
                                else "",
                                **describe_exception(e),
                            }
                        ),
                    )
                    discarded_count += 1
                    if len(discarded_examples) < 3:
                        discarded_examples.append({
                            "index": idx,
                            "issue": raw_issue,
                            "reason": f"exception: {str(e)}"
                        })
            
            # 记录丢弃统计
            if discarded_count > 0:
                self.ai_errors.append({
                    "type": "items_discarded",
                    "count": discarded_count,
                    "total_items": len(raw_issues),
                    "examples": discarded_examples,
                    "message": f"因格式问题丢弃了 {discarded_count}/{len(raw_issues)} 个AI检测结果"
                })
                logger.warning(f"因格式问题丢弃了 {discarded_count}/{len(raw_issues)} 个AI检测结果")
            
            return issues
            
        except json.JSONDecodeError as e:
            logger.error(f"AI响应JSON解析失败: {e}")
            self.ai_errors.append({
                "type": "json_decode_error",
                "message": f"JSON解析失败: {str(e)}",
                "response_preview": response[:200] + "..." if len(response) > 200 else response
            })
            return []
        except Exception as e:
            logger.error(f"解析AI响应失败: {e}")
            self.ai_errors.append({
                "type": "parse_error",
                "message": f"解析失败: {str(e)}",
                "response_preview": response[:200] + "..." if len(response) > 200 else response
            })
            return []
    
    def _extract_json_from_response(self, response: str) -> Optional[str]:
        """从响应中提取JSON内容"""
        # 尝试直接解析
        response = response.strip()
        if response.startswith('[') and response.endswith(']'):
            return response
        
        # 尝试从代码块中提取
        json_pattern = r'```(?:json)?\s*(\[.*?\])\s*```'
        match = re.search(json_pattern, response, re.DOTALL)
        if match:
            return match.group(1)
        
        # 尝试查找数组结构
        array_pattern = r'(\[.*?\])'
        match = re.search(array_pattern, response, re.DOTALL)
        if match:
            return match.group(1)
        
        return None
    
    def _convert_ai_issue(self, raw_issue: Dict[str, Any], context: JobContext, idx: int) -> Optional[IssueItem]:
        """转换AI问题为IssueItem"""
        try:
            # ZC-00（SYS-07）：AI 没给出有效页码时保持 None，不得默认第 1 页——
            # 假页码会让证据门把无定位条目当成已定位。
            page_number: Optional[int] = None
            try:
                raw_page = int(str(raw_issue.get("page")).strip())
                page_count = len(context.page_texts or [])
                if raw_page > 0 and (page_count == 0 or raw_page <= page_count):
                    page_number = raw_page
            except (TypeError, ValueError):
                page_number = None
            item = self._build_ai_issue_item(
                raw_issue=raw_issue,
                context=context,
                idx=idx,
                page_number=page_number,
            )
            if "category" in raw_issue:
                item.tags = list(dict.fromkeys([*item.tags, str(raw_issue["category"])]))
            return item
            
        except Exception as e:
            logger.warning(
                "转换AI问题失败",
                extra=safe_log_extra(
                    {
                        "issue_index": idx,
                        "issue_rule_id": str(raw_issue.get("rule_id") or ""),
                        "issue_field_names": sorted(str(key) for key in raw_issue),
                        **describe_exception(e),
                    }
                ),
            )
            self.ai_errors.append({
                "type": "conversion_error",
                "error_type": type(e).__name__,
                "issue_index": idx,
            })
            return None
    
    def _normalize_severity(self, severity: str) -> str:
        """标准化严重程度"""
        return normalize_ai_severity(severity)


async def analyze_with_ai(context: JobContext, config: AnalysisConfig) -> List[IssueItem]:
    """使用AI分析的便捷函数"""
    service = AIFindingsService(config)
    return await service.analyze(context)
