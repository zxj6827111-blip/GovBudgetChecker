"""
寮曟搸瑙勫垯杩愯鍣?
灏佽鐜版湁鐨?engine/rules_v33锛岀粺涓€杈撳嚭鏍煎紡涓?IssueItem
"""

import logging
import time
import uuid
from pathlib import Path
from typing import Dict, Any, List, Optional

from src.schemas.issues import JobContext, AnalysisConfig, IssueItem
from src.engine.rules_v33 import ALL_RULES as FINAL_ALL_RULES, build_document, Issue, Document
from src.engine.budget_rules import ALL_BUDGET_RULES
from src.engine.common_rules import ALL_COMMON_RULES
from src.engine.rule_outcome import (
    RuleOutcome,
    RuleOutcomeSignal,
    STATUS_PARSE_ERROR,
    summarize_rule_outcomes,
)
from src.utils.issue_bbox import PDFBBoxLocator
from src.utils.issue_location import normalize_issue_location
from src.utils.logging_config import describe_exception, safe_log_extra
from src.utils.provenance import ENGINE_VERSION
from src.utils.rule_text import default_rule_suggestion, infer_rule_title

logger = logging.getLogger(__name__)


class EngineRuleRunner:
    """Execute local engine rules and normalize findings."""

    def __init__(self):
        self._stats = {
            "total_rules": 0,
            "successful_rules": 0,
            "failed_rules": 0,
        }
        self._outcomes: List[RuleOutcome] = []

    @staticmethod
    def _normalize_severity(raw_severity: Any) -> str:
        severity_map = {
            "critical": "critical",
            "fatal": "critical",
            "error": "high",
            "high": "high",
            "warn": "medium",
            "warning": "medium",
            "medium": "medium",
            "manual_review": "manual_review",
            "low": "low",
            "hint": "low",
            "info": "info",
        }
        value = str(raw_severity or "medium").strip().lower()
        return severity_map.get(value, "medium")

    @staticmethod
    def _normalize_page(page: Any) -> int:
        if isinstance(page, bool):
            return 1
        if isinstance(page, int):
            return page if page > 0 else 1
        try:
            parsed = int(str(page).strip())
            return parsed if parsed > 0 else 1
        except Exception:
            return 1

    def _build_evidence_list(self, issue: Issue, page_number: int) -> List[Dict[str, Any]]:
        text = (
            getattr(issue, "evidence_text", None)
            or getattr(issue, "description", None)
            or issue.message
        )
        evidence_item: Dict[str, Any] = {
            "page": page_number,
            "text": text,
            "text_snippet": text,
        }
        bbox = getattr(issue, "bbox", None)
        if bbox:
            evidence_item["bbox"] = bbox
        return [evidence_item]

    def _issue_to_finding(
        self,
        issue: Issue,
        rule_id: Optional[str] = None,
        document: Optional[Document] = None,
        rule_version: Optional[str] = None,
    ) -> IssueItem:
        raw_location = getattr(issue, "location", {}) or {}
        location = normalize_issue_location(
            rule_id=rule_id or getattr(issue, "rule", ""),
            location=raw_location,
            message=getattr(issue, "message", "") or "",
            evidence_text=getattr(issue, "evidence_text", None)
            or getattr(issue, "message", "")
            or "",
            document=document,
        )
        page_number = self._normalize_page(
            location.get("page", getattr(issue, "page_number", None))
        )
        location.setdefault("page", page_number)

        resolved_rule_id = str(rule_id or getattr(issue, "rule", "") or "UNKNOWN")
        title = infer_rule_title(resolved_rule_id, getattr(issue, "message", "") or "")
        suggestion: Optional[str] = getattr(issue, "suggestion", None) or default_rule_suggestion(
            resolved_rule_id, page_number
        )
        if "page" not in raw_location and "pages" not in location:
            suggestion = (
                f"{suggestion} 当前页码未精确定位，可结合证据片段检索原文复核。"
                if suggestion
                else "当前页码未精确定位，可结合证据片段检索原文复核。"
            )

        return IssueItem(
            id=str(uuid.uuid4()),
            source="rule",
            rule_id=resolved_rule_id,
            severity=self._normalize_severity(getattr(issue, "severity", "medium")),
            title=title,
            message=getattr(issue, "message", "") or "",
            evidence=self._build_evidence_list(issue, page_number),
            location=location,
            page_number=page_number,
            suggestion=suggestion,
            tags=[resolved_rule_id] if resolved_rule_id else [],
            # 版本留痕（P2-02）：规则来源的 finding 记录本次实际使用的规则集版本
            # 与引擎版本；模型/提示词版本留空，因为这条问题不经过任何 AI 调用。
            rule_version=rule_version,
            engine_version=ENGINE_VERSION,
        )

    def _resolve_report_kind(
        self,
        job_context: JobContext,
        document: Optional[Document] = None,
    ) -> str:
        """
        Resolve report kind:
        1) job_context.meta.report_kind
        2) filename hint
        3) first page text hint

        Never use the full filesystem path for inference.  The project and
        upload directories may themselves contain words such as "budget",
        which are unrelated to the uploaded material.
        """
        meta = job_context.meta or {}
        report_kind = str(meta.get("report_kind") or "").strip().lower()
        if report_kind in {"budget", "final"}:
            return report_kind

        filename = Path(job_context.pdf_path).name.lower()
        # 关键词优先级与 src/engine/pipeline.py 保持一致：budget 优先。
        if "budget" in filename or "\u9884\u7b97" in filename:
            return "budget"
        if "final" in filename or "\u51b3\u7b97" in filename:
            return "final"

        if document and document.page_texts:
            first_text = document.page_texts[0] or ""
            if "\u9884\u7b97" in first_text:
                return "budget"
            if "\u51b3\u7b97" in first_text:
                return "final"

        return "unknown"

    def _select_rule_set(
        self,
        job_context: JobContext,
        document: Optional[Document] = None,
    ) -> List[Any]:
        report_kind = self._resolve_report_kind(job_context, document)
        if report_kind == "budget":
            return [*ALL_BUDGET_RULES, *ALL_COMMON_RULES]
        if report_kind == "final":
            return [*FINAL_ALL_RULES, *ALL_COMMON_RULES]
        # A material whose type cannot be established must not be checked
        # against either type-specific rule set.  Common rules still provide
        # low-risk feedback while the UI requests human confirmation.
        return list(ALL_COMMON_RULES)

    async def run_rules(
        self, job_context: JobContext, rules: List[Dict[str, Any]], config: AnalysisConfig
    ) -> List[IssueItem]:
        """
        杩愯寮曟搸瑙勫垯妫€鏌?

        Args:
            job_context: 浣滀笟涓婁笅鏂?
            rules: 寮曟搸瑙勫垯鍒楄〃
            config: 鍒嗘瀽閰嶇疆

        Returns:
            List[IssueItem]: 妫€鏌ョ粨鏋滃垪琛?
        """
        # Prepare document object and select rules by report kind.
        document = await self._prepare_document(job_context)
        selected_rules = self._select_rule_set(job_context, document)
        report_kind = self._resolve_report_kind(job_context, document)
        # 版本留痕（P2-02）：本次实际生效的规则集版本，逐条写进 finding。
        rule_version = str(getattr(config, "rules_version", "") or "").strip() or None

        logger.info(
            f"Using {len(selected_rules)} rules for job {job_context.job_id}, "
            f"report_kind={report_kind}"
        )

        all_findings = []
        self._stats = {
            "total_rules": len(selected_rules),
            "successful_rules": 0,
            "failed_rules": 0,
            "total_findings": 0,
        }
        self._outcomes = []

        bbox_locator = PDFBBoxLocator(job_context.pdf_path)
        try:
            # Execute selected rule set.
            for rule_obj in selected_rules:
                rule_id = rule_obj.code

                try:
                    start_time = time.time()

                    # 鐩存帴璋冪敤瑙勫垯瀵硅薄鐨刟pply鏂规硶
                    issues = rule_obj.apply(document)

                    int((time.time() - start_time) * 1000)

                    # 杞崲涓篒ssueItem鏍煎紡
                    findings = []
                    conversion_errors = []
                    for issue in issues:
                        try:
                            finding = self._issue_to_finding(
                                issue,
                                rule_id=rule_id,
                                document=document,
                                rule_version=rule_version,
                            )
                            finding = bbox_locator.locate(finding)
                            findings.append(finding)
                        except Exception as e:
                            # 这里的异常多来自 IssueItem 校验失败，pydantic 会把输入值
                            # （证据原文片段）写进异常消息，异常栈里也带着同一段消息，
                            # 所以刻意不用 logger.exception，只留错误类型、字段路径与指纹。
                            logger.error(
                                "Failed to convert issue to IssueItem",
                                extra=safe_log_extra({"rule_id": rule_id, **describe_exception(e)}),
                            )
                            conversion_errors.append(type(e).__name__)
                            continue

                    if conversion_errors:
                        # 规则本身跑完不等于结果可信：只要有一条 issue 在
                        # IssueItem 边界被丢弃，就必须进入 parse_error 摘要，
                        # 由质量门转人工复核，不能把“部分输出”记成 pass。
                        self._stats["failed_rules"] += 1
                        self._outcomes.append(
                            RuleOutcome(
                                rule_id=str(rule_id),
                                status=STATUS_PARSE_ERROR,
                                detail=(
                                    f"{len(conversion_errors)} 个 finding 转换失败，"
                                    f"异常类型={sorted(set(conversion_errors))}"
                                ),
                            )
                        )
                    else:
                        self._stats["successful_rules"] += 1
                        self._outcomes.append(
                            RuleOutcome(
                                rule_id=str(rule_id),
                                status="fail" if findings else "pass",
                            )
                        )
                    all_findings.extend(findings)
                    self._stats["total_findings"] += len(findings)

                    logger.debug(f"Rule {rule_id} found {len(findings)} issues")

                except RuleOutcomeSignal as signal:
                    # 非 fail 结局（insufficient_data/not_applicable 等）：
                    # 不产出 finding，也不算失败，进规则执行摘要供质量门消费
                    self._outcomes.append(
                        RuleOutcome(
                            rule_id=str(rule_id),
                            status=signal.status,
                            detail=str(signal.detail or signal),
                        )
                    )
                    logger.info(
                        "Rule %s deferred: %s",
                        rule_id,
                        signal.status,
                        extra=safe_log_extra({"rule_id": rule_id, "outcome": signal.status}),
                    )
                    continue
                except Exception as e:
                    self._stats["failed_rules"] += 1
                    self._outcomes.append(
                        RuleOutcome(
                            rule_id=str(rule_id),
                            status="execution_error",
                            detail=f"{type(e).__name__}: {e}",
                        )
                    )
                    logger.exception(
                        "Rule execution failed",
                        extra=safe_log_extra({"rule_id": rule_id, **describe_exception(e)}),
                    )

                    # 鍒涘缓澶辫触璁板綍
                    if config.record_rule_failures:
                        failure_item = IssueItem(
                            id=str(uuid.uuid4()),
                            source="rule",
                            rule_id=rule_id,
                            title=f"Rule execution failed: {rule_obj.desc}",
                            message=f"Rule execution error: {str(e)}",
                            severity="low",
                            location={"page": 1},
                            page_number=1,
                            evidence=[
                                {
                                    "page": 1,
                                    "text": f"Execution error: {str(e)}",
                                    "text_snippet": f"Execution error: {str(e)}",
                                }
                            ],
                            why_not=f"EXECUTION_ERROR: {str(e)}",
                            rule_version=rule_version,
                            engine_version=ENGINE_VERSION,
                        )
                        all_findings.append(failure_item)
        finally:
            bbox_locator.close()

        logger.info(
            f"Engine rules completed: {len(all_findings)} findings from {len(selected_rules)} rules "
            f"(success: {self._stats['successful_rules']}, failed: {self._stats['failed_rules']})"
        )

        return all_findings

    async def _prepare_document(self, job_context: JobContext) -> Document:
        """鍑嗗鏂囨。瀵硅薄"""

        # 1. 浼樺厛浣跨敤 JobContext 涓殑 page_texts 鍜?page_tables锛堢簿纭〉鐮侊級
        if hasattr(job_context, "page_texts") and job_context.page_texts:
            logger.info(
                f"Using page_texts from JobContext for job {job_context.job_id} ({len(job_context.page_texts)} pages)"
            )

            page_texts = job_context.page_texts
            page_tables = getattr(job_context, "page_tables", []) or []

            # 纭繚 page_tables 涓?page_texts 闀垮害涓€鑷?
            while len(page_tables) < len(page_texts):
                page_tables.append([])

            return build_document(
                path=job_context.pdf_path,
                page_texts=page_texts,
                page_tables=page_tables,
                filesize=getattr(job_context, "filesize", 0) or job_context.meta.get("filesize", 0),
            )

        # 2. 鍥為€€锛氬皾璇曚粠 ocr_text 鍜?tables 鎭㈠锛堝吋瀹规棫鏁版嵁锛?
        if job_context.ocr_text and job_context.tables:
            logger.info(f"Restoring document from JobContext ocr_text for job {job_context.job_id}")

            page_texts = []
            page_tables = []

            # 灏?ocr_text 鎸夐〉鎷嗗垎锛堝鏋滃彲鑳斤級
            if job_context.pages > 0 and "\n\n" in job_context.ocr_text:
                parts = job_context.ocr_text.split("\n\n")
                if len(parts) == job_context.pages:
                    page_texts = parts

            # 濡傛灉娌℃媶鎴愶紝灏卞叏閮ㄦ斁杩涚涓€椤?
            if not page_texts:
                page_texts = [job_context.ocr_text]

            # 鎭㈠琛ㄦ牸鏁版嵁
            num_pages = job_context.pages or len(job_context.tables) or 1
            temp_tables = [[] for _ in range(num_pages)]

            for item in job_context.tables:
                p_idx = item.get("page", 1) - 1
                if 0 <= p_idx < len(temp_tables):
                    temp_tables[p_idx] = item.get("tables", [])

            page_tables = temp_tables

            return build_document(
                path=job_context.pdf_path,
                page_texts=page_texts,
                page_tables=page_tables,
                filesize=job_context.meta.get("filesize", 0),
            )

        # 2. 濡傛灉 job_context 涓病鏈夋暟鎹紝鍒欏疄闄呰В鏋?PDF 鏂囦欢
        logger.warning(f"JobContext missing data, re-parsing PDF: {job_context.pdf_path}")
        import pdfplumber
        import os
        from src.services.pdf_page_extract import (
            extract_tables_from_page,
            extract_visible_text_from_page,
        )

        page_texts = []
        page_tables = []
        filesize = 0

        try:
            if os.path.exists(job_context.pdf_path):
                filesize = os.path.getsize(job_context.pdf_path)
                with pdfplumber.open(job_context.pdf_path) as pdf:
                    job_context.pages = len(pdf.pages)
                    for page in pdf.pages:
                        page_texts.append(extract_visible_text_from_page(page))
                        # fallback 也必须复用带 bbox/表名锚的统一抽取器，
                        # 否则 structured 规则会在该入口重新丢失表边界。
                        page_tables.append(extract_tables_from_page(page))
            else:
                logger.error(f"PDF鏂囦欢涓嶅瓨鍦? {job_context.pdf_path}")
        except Exception as e:
            logger.exception(
                "parse pdf for rule engine failed",
                extra=safe_log_extra(describe_exception(e)),
            )

        return build_document(
            path=job_context.pdf_path,
            page_texts=page_texts,
            page_tables=page_tables,
            filesize=filesize,
        )

    def _apply_tolerance(
        self, findings: List[IssueItem], tolerance: Dict[str, Any]
    ) -> List[IssueItem]:
        """搴旂敤瀹瑰樊璁剧疆杩囨护缁撴灉"""

        filtered_findings = []

        money_rel = tolerance.get("money_rel", 0.005)  # 榛樿 0.5%
        pct_abs = tolerance.get("pct_abs", 0.002)  # 榛樿 0.2pp

        for finding in findings:
            should_include = True

            # 閲戦瀹瑰樊妫€鏌?
            if finding.amount is not None and money_rel > 0:
                # 杩欓噷闇€瑕佹牴鎹叿浣撶殑涓氬姟閫昏緫瀹炵幇瀹瑰樊妫€鏌?
                # 鏆傛椂淇濈暀鎵€鏈夐噾棰濈浉鍏崇殑闂
                pass

            # 姣斾緥瀹瑰樊妫€鏌?
            if finding.percentage is not None and pct_abs > 0:
                # 杩欓噷闇€瑕佹牴鎹叿浣撶殑涓氬姟閫昏緫瀹炵幇瀹瑰樊妫€鏌?
                # 鏆傛椂淇濈暀鎵€鏈夋瘮渚嬬浉鍏崇殑闂
                pass

            if should_include:
                filtered_findings.append(finding)
            else:
                # 鏇存柊 why_not 璇存槑琚宸繃婊?
                finding.why_not = f"TOLERANCE_FILTERED: money_rel={money_rel}, pct_abs={pct_abs}"

        return filtered_findings

    def get_stats(self) -> Dict[str, Any]:
        """鑾峰彇鎵ц缁熻"""
        return self._stats.copy()

    def get_rule_execution_summary(self) -> Dict[str, Any]:
        """规则执行摘要（RuleOutcome 六态汇总），供 result.meta 与质量门消费。"""
        return summarize_rule_outcomes(self._outcomes)

    def clear_stats(self):
        """娓呴櫎缁熻淇℃伅"""
        self._stats = {
            "total_rules": 0,
            "successful_rules": 0,
            "failed_rules": 0,
            "total_findings": 0,
        }


# 渚挎嵎鍑芥暟
async def run_engine_rules(
    job_context: JobContext, rules: List[Dict[str, Any]], config: Optional[AnalysisConfig] = None
) -> List[IssueItem]:
    """Convenience wrapper for running engine rules."""
    if config is None:
        from src.schemas.issues import AnalysisConfig

        config = AnalysisConfig()

    runner = EngineRuleRunner()
    return await runner.run_rules(job_context, rules, config)


def get_available_rules() -> List[str]:
    """Return all available engine rule codes."""
    return [rule.code for rule in (ALL_BUDGET_RULES + FINAL_ALL_RULES + ALL_COMMON_RULES)]


def validate_rule_id(rule_id: str) -> bool:
    """楠岃瘉瑙勫垯ID鏄惁鏈夋晥"""
    return any(
        rule.code == rule_id or rule_id in rule.code
        for rule in (ALL_BUDGET_RULES + FINAL_ALL_RULES + ALL_COMMON_RULES)
    )
