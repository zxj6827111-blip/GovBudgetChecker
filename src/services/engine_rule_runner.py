"""
引擎规则运行器
封装现有的 engine/rules_v33，统一输出格式为 IssueItem
"""
import logging
import time
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

from src.schemas.issues import JobContext, AnalysisConfig, IssueItem
from src.engine.rules_v33 import ALL_RULES as FINAL_ALL_RULES, build_document, Issue, Document
from src.engine.budget_rules import ALL_BUDGET_RULES

logger = logging.getLogger(__name__)


@dataclass
class EngineRuleResult:
    """引擎规则执行结果"""
    rule_id: str
    success: bool
    findings: List[IssueItem]
    why_not: Optional[str] = None
    elapsed_ms: int = 0


class EngineRuleRunner:
    """引擎规则运行器"""
    
    def __init__(self):
        self._stats = {
            "total_rules": 0,
            "successful_rules": 0,
            "failed_rules": 0,
        }

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
        """
        meta = job_context.meta or {}
        report_kind = str(meta.get("report_kind") or "").strip().lower()
        if report_kind in {"budget", "final"}:
            return report_kind

        source_text = f"{job_context.pdf_path} {job_context.job_id}".lower()
        if "budget" in source_text or "预算" in source_text:
            return "budget"
        if "final" in source_text or "决算" in source_text:
            return "final"

        if document and document.page_texts:
            first_text = (document.page_texts[0] or "")
            if "预算" in first_text:
                return "budget"
            if "决算" in first_text:
                return "final"

        return "final"

    def _select_rule_set(
        self,
        job_context: JobContext,
        document: Optional[Document] = None,
    ) -> List[Any]:
        report_kind = self._resolve_report_kind(job_context, document)
        if report_kind == "budget":
            return ALL_BUDGET_RULES
        return FINAL_ALL_RULES
    
    async def run_rules(self, 
                       job_context: JobContext,
                       rules: List[Dict[str, Any]],
                       config: AnalysisConfig) -> List[IssueItem]:
        """
        运行引擎规则检查
        
        Args:
            job_context: 作业上下文
            rules: 引擎规则列表
            config: 分析配置
            
        Returns:
            List[IssueItem]: 检查结果列表
        """
        # 准备文档对象并按报告类型选择规则集
        document = await self._prepare_document(job_context)
        selected_rules = self._select_rule_set(job_context, document)
        report_kind = self._resolve_report_kind(job_context, document)

        logger.info(
            f"Using {len(selected_rules)} rules for job {job_context.job_id}, "
            f"report_kind={report_kind}"
        )

        all_findings = []
        self._stats = {
            "total_rules": len(selected_rules),
            "successful_rules": 0,
            "failed_rules": 0,
            "total_findings": 0
        }

        # 执行选定规则集
        for rule_obj in selected_rules:
            rule_id = rule_obj.code
            
            try:
                start_time = time.time()
                
                # 直接调用规则对象的apply方法
                issues = rule_obj.apply(document)
                
                elapsed_ms = int((time.time() - start_time) * 1000)
                
                # 转换为IssueItem格式
                findings = []
                for issue in issues:
                    try:
                        # 创建符合IssueItem要求的字段
                        severity_map = {
                            "error": "high",
                            "warn": "medium",
                            "warning": "medium",
                            "info": "info",
                            "hint": "low"
                        }
                        
                        severity = issue.severity if hasattr(issue, 'severity') else "medium"
                        severity = severity_map.get(severity.lower(), "medium")
                        
                        # 生成唯一ID
                        import uuid
                        issue_id = str(uuid.uuid4())
                        
                        finding = IssueItem(
                            id=issue_id,
                            rule_id=issue.rule,
                            title=issue.message,
                            description=issue.message,
                            severity=severity,
                            page_number=issue.location.get("page", 1) if hasattr(issue, 'location') else 1,
                            evidence=[{"text": getattr(issue, 'evidence_text', '') or getattr(issue, 'description', '') or issue.message}],
                            source="rule",
                            job_id=job_context.job_id,
                            message=issue.message  # 添加必填的message字段
                        )
                        findings.append(finding)
                    except Exception as e:
                        logger.error(f"Failed to convert issue to IssueItem: {e}")
                        import traceback
                        logger.error(f"Conversion error details: {traceback.format_exc()}")
                        continue
                
                self._stats["successful_rules"] += 1
                all_findings.extend(findings)
                self._stats["total_findings"] += len(findings)
                
                logger.debug(f"Rule {rule_id} found {len(findings)} issues")
                
            except Exception as e:
                self._stats["failed_rules"] += 1
                logger.error(f"Rule {rule_id} execution failed: {e}")
                import traceback
                logger.error(f"Exception details: {traceback.format_exc()}")
                
                # 创建失败记录
                if config.record_rule_failures:
                    failure_item = IssueItem(
                        rule_id=rule_id,
                        title=f"规则执行失败: {rule_obj.desc}",
                        description=f"规则执行过程中发生错误: {str(e)}",
                        severity="low",
                        page_number=1,
                        evidence={"text_snippet": f"执行错误: {str(e)}"},
                        source="rule",
                        job_id=job_context.job_id,
                        why_not=f"EXECUTION_ERROR: {str(e)}"
                    )
                    all_findings.append(failure_item)
        
        logger.info(f"Engine rules completed: {len(all_findings)} findings from {len(selected_rules)} rules "
                   f"(success: {self._stats['successful_rules']}, failed: {self._stats['failed_rules']})")
        
        return all_findings
    
    async def _prepare_document(self, job_context: JobContext) -> Document:
        """准备文档对象"""
        
        # 1. 优先使用 JobContext 中的 page_texts 和 page_tables（精确页码）
        if hasattr(job_context, 'page_texts') and job_context.page_texts:
            logger.info(f"Using page_texts from JobContext for job {job_context.job_id} ({len(job_context.page_texts)} pages)")
            
            page_texts = job_context.page_texts
            page_tables = getattr(job_context, 'page_tables', []) or []
            
            # 确保 page_tables 与 page_texts 长度一致
            while len(page_tables) < len(page_texts):
                page_tables.append([])
            
            return build_document(
                path=job_context.pdf_path,
                page_texts=page_texts,
                page_tables=page_tables,
                filesize=getattr(job_context, 'filesize', 0) or job_context.meta.get("filesize", 0)
            )
        
        # 2. 回退：尝试从 ocr_text 和 tables 恢复（兼容旧数据）
        if job_context.ocr_text and job_context.tables:
            logger.info(f"Restoring document from JobContext ocr_text for job {job_context.job_id}")
            
            page_texts = []
            page_tables = []
            
            # 将 ocr_text 按页拆分（如果可能）
            if job_context.pages > 0 and "\n\n" in job_context.ocr_text:
                parts = job_context.ocr_text.split("\n\n")
                if len(parts) == job_context.pages:
                    page_texts = parts
            
            # 如果没拆成，就全部放进第一页
            if not page_texts:
                page_texts = [job_context.ocr_text]
            
            # 恢复表格数据
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
                filesize=job_context.meta.get("filesize", 0)
            )

        # 2. 如果 job_context 中没有数据，则实际解析 PDF 文件
        logger.warning(f"JobContext missing data, re-parsing PDF: {job_context.pdf_path}")
        import pdfplumber
        import os
        
        page_texts = []
        page_tables = []
        filesize = 0
        
        try:
            if os.path.exists(job_context.pdf_path):
                filesize = os.path.getsize(job_context.pdf_path)
                with pdfplumber.open(job_context.pdf_path) as pdf:
                    job_context.pages = len(pdf.pages)
                    for page in pdf.pages:
                        page_texts.append(page.extract_text() or "")
                        # ✅ 修复：使用更稳健的表格提取策略，与 api/main.py 保持一致
                        tables = []
                        try:
                            # 尝试线策略
                            rows = page.extract_tables(table_settings={
                                "vertical_strategy": "lines",
                                "horizontal_strategy": "lines",
                                "intersection_tolerance": 3,
                            }) or []
                            tables.extend(rows)
                        except:
                            pass
                        
                        if not tables:
                            # 退填默认策略
                            tables = page.extract_tables() or []
                            
                        page_tables.append(tables)
            else:
                logger.error(f"PDF文件不存在: {job_context.pdf_path}")
        except Exception as e:
            logger.error(f"解析PDF文件失败: {e}")
            import traceback
            logger.error(f"解析错误详情: {traceback.format_exc()}")
        
        return build_document(
            path=job_context.pdf_path,
            page_texts=page_texts,
            page_tables=page_tables,
            filesize=filesize
        )
    
    async def _execute_rule(self, 
                           rule: Dict[str, Any],
                           document: Document,
                           job_context: JobContext,
                           config: AnalysisConfig) -> EngineRuleResult:
        """执行单个规则"""
        
        start_time = time.time()
        rule_id = rule.get('id', 'unknown')
        
        try:
            # 查找对应的规则对象
            rule_obj = None
            available_rules = self._select_rule_set(job_context, document)
            for r in available_rules:
                if r.code == rule_id or rule_id in r.code:
                    rule_obj = r
                    break
            
            if rule_obj is None:
                return EngineRuleResult(
                    rule_id=rule_id,
                    success=False,
                    findings=[],
                    why_not=f"NO_RULE: Rule object not found for {rule_id}",
                    elapsed_ms=int((time.time() - start_time) * 1000)
                )
            
            # 执行规则
            issues = rule_obj.apply(document)
            
            # 转换为 IssueItem 格式
            findings = []
            
            if issues:
                for issue in issues:
                    if isinstance(issue, Issue):
                        finding = self._convert_issue_to_item(
                            issue=issue,
                            rule=rule,
                            job_context=job_context
                        )
                        findings.append(finding)
                    else:
                        logger.warning(f"Rule {rule_id} returned non-Issue object: {type(issue)}")
            
            # 应用容差设置
            if rule.get('tolerance') and findings:
                findings = self._apply_tolerance(findings, rule['tolerance'])
            
            elapsed_ms = int((time.time() - start_time) * 1000)
            
            return EngineRuleResult(
                rule_id=rule_id,
                success=True,
                findings=findings,
                why_not=None if findings else "NO_ISSUES_FOUND",
                elapsed_ms=elapsed_ms
            )
            
        except Exception as e:
            # 分析失败原因，并添加详细日志
            import traceback
            logger.error(f"Rule {rule_id} execution failed: {e}")
            logger.error(f"Exception details: {traceback.format_exc()}")
            
            why_not = self._analyze_failure_reason(e, rule_id)
            elapsed_ms = int((time.time() - start_time) * 1000)
            
            return EngineRuleResult(
                rule_id=rule_id,
                success=False,
                findings=[],
                why_not=why_not,
                elapsed_ms=elapsed_ms
            )
    
    def _convert_issue_to_item(self, 
                              issue: Issue,
                              rule: Dict[str, Any],
                              job_context: JobContext) -> IssueItem:
        """将 Issue 对象转换为 IssueItem"""
        
        # 提取证据信息
        evidence = {
            "text": getattr(issue, 'evidence_text', '') or getattr(issue, 'description', '') or issue.message,
        }
        
        # 如果有坐标信息，添加 bbox
        if hasattr(issue, 'bbox') and issue.bbox:
            evidence["bbox"] = issue.bbox
        
        # 确定严重程度
        severity = getattr(issue, 'severity', 'medium')
        if severity not in ['high', 'medium', 'low']:
            severity = 'medium'
        
        # 提取金额和比例
        amount = getattr(issue, 'amount', None)
        percentage = getattr(issue, 'percentage', None)
        
        # 提取页码
        page_number = getattr(issue, 'page_number', 1)
        if not isinstance(page_number, int) or page_number < 1:
            page_number = 1
        
        # 提取标签
        tags = getattr(issue, 'tags', []) or []
        if isinstance(tags, str):
            tags = [tags]
        
        return IssueItem(
            rule_id=rule.get('id', 'unknown'),
            title=getattr(issue, 'title', '') or rule.get('title', '未知问题'),
            description=getattr(issue, 'description', '') or rule.get('description', ''),
            severity=severity,
            page_number=page_number,
            evidence=evidence,
            amount=amount,
            percentage=percentage,
            tags=tags,
            source="rule",
            job_id=job_context.job_id,
            why_not=None
        )
    
    def _apply_tolerance(self, 
                        findings: List[IssueItem], 
                        tolerance: Dict[str, Any]) -> List[IssueItem]:
        """应用容差设置过滤结果"""
        
        filtered_findings = []
        
        money_rel = tolerance.get('money_rel', 0.005)  # 默认 0.5%
        pct_abs = tolerance.get('pct_abs', 0.002)      # 默认 0.2pp
        
        for finding in findings:
            should_include = True
            
            # 金额容差检查
            if finding.amount is not None and money_rel > 0:
                # 这里需要根据具体的业务逻辑实现容差检查
                # 暂时保留所有金额相关的问题
                pass
            
            # 比例容差检查
            if finding.percentage is not None and pct_abs > 0:
                # 这里需要根据具体的业务逻辑实现容差检查
                # 暂时保留所有比例相关的问题
                pass
            
            if should_include:
                filtered_findings.append(finding)
            else:
                # 更新 why_not 说明被容差过滤
                finding.why_not = f"TOLERANCE_FILTERED: money_rel={money_rel}, pct_abs={pct_abs}"
        
        return filtered_findings
    
    def _analyze_failure_reason(self, error: Exception, rule_id: str) -> str:
        """分析失败原因"""
        
        error_str = str(error).lower()
        
        if "anchor" in error_str or "找不到" in error_str:
            return f"NO_ANCHOR: {str(error)}"
        elif "table" in error_str or "表格" in error_str:
            return f"TABLE_PARSE_FAIL: {str(error)}"
        elif "unit" in error_str or "单位" in error_str:
            return f"UNIT_MISMATCH: {str(error)}"
        elif "tolerance" in error_str or "容差" in error_str:
            return f"TOLERANCE_FAIL: {str(error)}"
        elif "keyerror" in error_str or "key" in error_str:
            return f"MISSING_DATA: {str(error)}"
        elif "valueerror" in error_str or "value" in error_str:
            return f"DATA_FORMAT_ERROR: {str(error)}"
        else:
            return f"UNKNOWN_ERROR: {str(error)}"
    
    def get_stats(self) -> Dict[str, Any]:
        """获取执行统计"""
        return self._stats.copy()
    
    def clear_stats(self):
        """清除统计信息"""
        self._stats = {
            "total_rules": 0,
            "successful_rules": 0,
            "failed_rules": 0,
            "total_findings": 0
        }


# 便捷函数
async def run_engine_rules(job_context: JobContext,
                          rules: List[Dict[str, Any]],
                          config: Optional[AnalysisConfig] = None) -> List[IssueItem]:
    """便捷的引擎规则运行函数"""
    if config is None:
        from src.schemas.issues import AnalysisConfig
        config = AnalysisConfig()
    
    runner = EngineRuleRunner()
    return await runner.run_rules(job_context, rules, config)


def get_available_rules() -> List[str]:
    """获取可用的规则列表"""
    return [rule.code for rule in (ALL_BUDGET_RULES + FINAL_ALL_RULES)]


def validate_rule_id(rule_id: str) -> bool:
    """验证规则ID是否有效"""
    return any(
        rule.code == rule_id or rule_id in rule.code
        for rule in (ALL_BUDGET_RULES + FINAL_ALL_RULES)
    )
