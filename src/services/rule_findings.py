"""
规则检查服务
封装现有规则引擎，转换结果为统一的 IssueItem 格式
"""
import logging
from typing import List, Dict, Any, Optional
import traceback
import time

from src.schemas.issues import IssueItem, JobContext, AnalysisConfig
from src.engine.rules_v33 import ALL_RULES, build_document, order_and_number_issues  # 导入规则相关功能
from src.engine.pipeline import run_rules, build_issues_payload  # 导入流水线功能

logger = logging.getLogger(__name__)


class RuleFindingsService:
    """规则检查服务"""
    
    def __init__(self, config: AnalysisConfig):
        self.config = config
        self.rules_engine = None
        self.pipeline = None
        self._init_engines()
    
    def _init_engines(self):
        """初始化规则引擎"""
        try:
            # 初始化现有的规则引擎和流水线
            self.rules_engine = ALL_RULES  # 使用规则列表
            logger.info("规则引擎初始化成功")
        except Exception as e:
            logger.error(f"规则引擎初始化失败: {e}")
            self.rules_engine = None
    
    async def analyze(self, context: JobContext) -> List[IssueItem]:
        """执行规则分析"""
        if not self.config.rule_enabled:
            logger.info("规则分析已禁用")
            return []
        
        start_time = time.time()
        logger.info(f"开始规则分析: job_id={context.job_id}")
        
        try:
            # 调用现有的规则引擎
            rule_results = await self._run_rules_engine(context)
            
            # 转换为统一格式
            issues = self._convert_to_issues(rule_results, context)
            
            elapsed = time.time() - start_time
            logger.info(f"规则分析完成: job_id={context.job_id}, issues={len(issues)}, elapsed={elapsed:.2f}s")
            
            return issues
            
        except Exception as e:
            logger.error(f"规则分析失败: job_id={context.job_id}, error={e}")
            logger.error(traceback.format_exc())
            return []
    
    async def _run_rules_engine(self, context: JobContext) -> List[Dict[str, Any]]:
        """运行规则引擎"""
        if not self.pipeline:
            logger.warning("规则引擎未初始化，返回空结果")
            return []
        
        try:
            # 这里需要根据实际的规则引擎接口调整
            # 假设现有引擎接受 PDF 路径并返回问题列表
            results = []
            
            # 模拟调用现有规则引擎的逻辑
            # 实际实现时需要根据现有代码调整
            if hasattr(self.pipeline, 'analyze_pdf'):
                raw_results = await self.pipeline.analyze_pdf(context.pdf_path)
                results = raw_results if isinstance(raw_results, list) else []
            else:
                # 如果没有异步方法，使用同步方法
                import asyncio
                raw_results = await asyncio.get_event_loop().run_in_executor(
                    None, self._sync_analyze, context
                )
                results = raw_results if isinstance(raw_results, list) else []
            
            logger.info(f"规则引擎返回 {len(results)} 个结果")
            return results
            
        except Exception as e:
            logger.error(f"规则引擎执行失败: {e}")
            return []
    
    def _sync_analyze(self, context: JobContext) -> List[Dict[str, Any]]:
        """同步分析方法（兼容现有代码）"""
        try:
            # 这里需要根据实际的现有代码调整
            # 假设有一个同步的分析方法
            if self.pipeline and hasattr(self.pipeline, 'process'):
                return self.pipeline.process(context.pdf_path)
            else:
                # 返回模拟结果用于测试
                return self._get_mock_results(context)
        except Exception as e:
            logger.error(f"同步分析失败: {e}")
            return []
    
    def _get_mock_results(self, context: JobContext) -> List[Dict[str, Any]]:
        """获取模拟结果（用于测试和开发）"""
        return [
            {
                "rule_id": "V33-001",
                "title": "预算表缺失",
                "message": "未找到预算收支总表",
                "severity": "high",
                "page": 1,
                "section": "预算表",
                "evidence": "第1页未发现预算收支总表",
                "suggestion": "请补充预算收支总表"
            },
            {
                "rule_id": "V33-002", 
                "title": "金额不一致",
                "message": "总收入与明细收入不符",
                "severity": "medium",
                "page": 3,
                "section": "收入明细",
                "evidence": "总收入1000万，明细收入950万",
                "metrics": {"expected": 10000000, "actual": 9500000, "diff": 500000},
                "suggestion": "请核对收入明细计算"
            }
        ]
    
    def _convert_to_issues(self, rule_results: List[Dict[str, Any]], context: JobContext) -> List[IssueItem]:
        """转换规则结果为统一的 IssueItem 格式"""
        issues = []
        
        for idx, result in enumerate(rule_results):
            try:
                issue = self._convert_single_result(result, context, idx)
                if issue:
                    issues.append(issue)
            except Exception as e:
                logger.error(f"转换规则结果失败: {e}, result={result}")
        
        return issues
    
    def _convert_single_result(self, result: Dict[str, Any], context: JobContext, idx: int) -> Optional[IssueItem]:
        """转换单个规则结果"""
        try:
            # 提取基本信息
            rule_id = result.get("rule_id", f"RULE-{idx:03d}")
            title = result.get("title", "未知问题")
            message = result.get("message", result.get("description", ""))
            severity = self._normalize_severity(result.get("severity", "medium"))
            
            # 构建位置信息
            location = {
                "page": result.get("page", 0),
                "section": result.get("section", ""),
                "table": result.get("table", ""),
                "row": result.get("row", ""),
                "col": result.get("col", "")
            }
            
            # 构建证据
            evidence = []
            if "evidence" in result:
                evidence_text = result["evidence"]
                evidence.append({
                    "page": location["page"],
                    "text": evidence_text,
                    "bbox": result.get("bbox")  # 如果有边界框信息
                })
            
            # 构建指标
            metrics = {}
            if "metrics" in result:
                metrics = result["metrics"]
            elif any(key in result for key in ["expected", "actual", "diff"]):
                metrics = {
                    "expected": result.get("expected"),
                    "actual": result.get("actual"), 
                    "diff": result.get("diff")
                }
            
            # 构建标签
            tags = result.get("tags", [])
            if "category" in result:
                tags.append(result["category"])
            if location["section"]:
                tags.append(location["section"])
            
            # 生成唯一ID
            issue_id = IssueItem.create_id("rule", rule_id, location)
            
            return IssueItem(
                id=issue_id,
                source="rule",
                rule_id=rule_id,
                severity=severity,
                title=title,
                message=message,
                evidence=evidence,
                location=location,
                metrics=metrics,
                suggestion=result.get("suggestion"),
                tags=tags,
                created_at=time.time()
            )
            
        except Exception as e:
            logger.error(f"转换单个结果失败: {e}")
            return None
    
    def _normalize_severity(self, severity: str) -> str:
        """标准化严重程度"""
        severity_map = {
            "critical": "critical",
            "high": "high", 
            "error": "high",
            "medium": "medium",
            "warning": "medium",
            "low": "low",
            "info": "info",
            "notice": "info"
        }
        
        return severity_map.get(severity.lower(), "medium")


async def analyze_with_rules(context: JobContext, config: AnalysisConfig) -> List[IssueItem]:
    """使用规则引擎分析的便捷函数"""
    service = RuleFindingsService(config)
    return await service.analyze(context)