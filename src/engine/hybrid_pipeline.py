"""
混合验证架构Pipeline集成
将三层验证系统集成到现有检查流程中
"""

import logging
import asyncio
from typing import List, Dict, Any, Optional
import time
from dataclasses import dataclass

from .hybrid_validator import (
    ValidationIssue, IssueSource, IssueSeverity, IssueConfidence,
    ValidationContext, HybridConfig
)
from .rule_adapter import create_rule_engine_validator, EnhancedRuleEngine
from .ai_validator import create_ai_validator, AIValidationConfig
from .intelligent_merger import create_intelligent_merger, MergeConfig, MergeStrategy
from .rules_v33 import Issue, order_and_number_issues

logger = logging.getLogger(__name__)

class HybridPipeline:
    """混合验证架构Pipeline"""
    
    def __init__(self, config: Optional[HybridConfig] = None):
        """初始化混合Pipeline"""
        self.config = config or HybridConfig()
        
        # 初始化三层验证组件
        try:
            # 第一层：规则引擎
            self.rule_validator = create_rule_engine_validator()
            
            # 第二层：AI验证器
            if self.config.ai_enabled:
                ai_config = AIValidationConfig(
                    confidence_threshold=self.config.ai_confidence_threshold,
                    validation_timeout=self.config.ai_timeout
                )
                self.ai_validator = create_ai_validator(ai_config)
            else:
                self.ai_validator = None
            
            # 第三层：智能合并器
            merge_config = MergeConfig(
                similarity_threshold=self.config.similarity_threshold,
                strategy=MergeStrategy.BALANCED
            )
            self.merger = create_intelligent_merger(merge_config)
            
            logger.info("混合Pipeline初始化成功")
            
        except Exception as e:
            logger.error(f"混合Pipeline初始化失败: {e}")
            raise
    
    async def run_hybrid_validation(
        self, 
        document: str, 
        doc_obj: Any = None,
        use_ai_assist: bool = True
    ) -> List[ValidationIssue]:
        """
        运行混合验证流程
        
        Args:
            document: 文档文本内容
            doc_obj: 原始文档对象，用于兼容现有规则
            use_ai_assist: 是否启用AI辅助
            
        Returns:
            验证结果列表
        """
        try:
            logger.info("开始混合验证流程")
            start_time = time.time()
            
            # 更新配置
            self.config.ai_enabled = use_ai_assist
            
            # 创建验证上下文
            context = ValidationContext(
                document_path="temp_doc",
                document_type="budget_report",
                pages_text=[document],
                extracted_data={},
                metadata={"doc_obj": doc_obj}
            )
            
            # 执行混合验证
            results = self.hybrid_engine.validate_document(context)
            
            elapsed = time.time() - start_time
            logger.info(f"混合验证完成，耗时{elapsed:.2f}秒，共{len(results)}个问题")
            
            return results
            
        except Exception as e:
            logger.error(f"混合验证流程出错: {e}")
            # 降级到传统规则引擎
            return await self._fallback_to_rules(doc_obj)
    
    async def _fallback_to_rules(self, doc_obj: Any) -> List[ValidationIssue]:
        """降级到传统规则引擎"""
        logger.warning("降级到传统规则引擎")
        try:
            if doc_obj:
                # 使用传统规则引擎
                from .pipeline import run_rules
                issues = run_rules(doc_obj, use_ai_assist=False)
                return self._convert_issues_to_results(issues)
            else:
                return []
        except Exception as e:
            logger.error(f"降级规则引擎也失败: {e}")
            return []
    
    def _convert_issues_to_results(self, issues: List[Issue]) -> List[ValidationIssue]:
        """将传统Issue转换为ValidationIssue"""
        results = []
        for issue in issues:
            try:
                # 确定问题来源
                source = IssueSource.RULE_ENGINE
                
                # 确定置信度
                confidence = self._map_confidence(issue.severity)
                
                # 确定严重程度
                severity = self._map_severity(issue.severity)
                
                result = ValidationIssue(
                    rule_id=issue.rule,
                    title=f"规则检查: {issue.rule}",
                    description=issue.message,
                    severity=severity,
                    confidence=confidence,
                    source=source,
                    text_snippet=str(issue.location)[:100],
                    metadata={
                        "rule": issue.rule,
                        "original_severity": issue.severity,
                        "source": "fallback_rules",
                        "original_issue": issue
                    }
                )
                results.append(result)
            except Exception as e:
                logger.warning(f"转换Issue时出错: {e}")
        
        return results
    
    def _map_severity(self, severity_str: str) -> IssueSeverity:
        """映射严重程度字符串到枚举"""
        severity = severity_str.lower()
        if severity in ("error", "fatal", "critical"):
            return IssueSeverity.ERROR
        elif severity in ("warn", "warning"):
            return IssueSeverity.WARNING
        else:
            return IssueSeverity.INFO
    
    def _map_confidence(self, severity: str) -> IssueConfidence:
        """映射严重程度到置信度"""
        severity = severity.lower()
        if severity in ("error", "fatal", "critical"):
            return IssueConfidence.HIGH
        elif severity in ("warn", "warning"):
            return IssueConfidence.MEDIUM
        else:
            return IssueConfidence.LOW

def convert_results_to_issues(results: List[ValidationIssue]) -> List[Issue]:
    """将ValidationIssue转换回Issue格式，保持向后兼容"""
    issues = []
    for result in results:
        try:
            # 映射置信度到严重程度
            severity = _map_confidence_to_severity(result.confidence)
            
            # 解析位置信息
            location = _parse_location(result.text_snippet or "")
            
            # 获取规则代码
            rule_code = result.rule_id or "HYBRID"
            
            issue = Issue(
                rule=rule_code,
                severity=severity,
                message=result.description,
                location=location
            )
            issues.append(issue)
            
        except Exception as e:
            logger.warning(f"转换ValidationIssue时出错: {e}")
    
    return issues

def _map_confidence_to_severity(confidence: IssueConfidence) -> str:
    """映射置信度到严重程度"""
    if confidence == IssueConfidence.HIGH:
        return "error"
    elif confidence == IssueConfidence.MEDIUM:
        return "warn"
    else:
        return "info"

def _parse_location(location_str: str) -> Dict[str, Any]:
    """解析位置字符串"""
    try:
        # 尝试解析JSON格式的位置
        import json
        if location_str.startswith("{"):
            return json.loads(location_str)
    except:
        pass
    
    # 默认位置格式
    return {"page": 1, "pos": 0, "clip": location_str[:50]}

# 全局Pipeline实例
_hybrid_pipeline = None

def get_hybrid_pipeline(config: Optional[HybridConfig] = None) -> HybridPipeline:
    """获取全局混合Pipeline实例"""
    global _hybrid_pipeline
    if _hybrid_pipeline is None:
        _hybrid_pipeline = HybridPipeline(config)
    return _hybrid_pipeline

async def run_hybrid_rules(doc, document_text: str = "", use_ai_assist: bool = False) -> List[Issue]:
    """
    异步运行混合验证规则
    
    Args:
        doc: 文档对象
        document_text: 文档文本内容
        use_ai_assist: 是否启用AI辅助
        
    Returns:
        Issue列表
    """
    try:
        pipeline = get_hybrid_pipeline()
        
        # 如果没有提供文档文本，尝试从doc对象提取
        if not document_text and hasattr(doc, 'page_texts'):
            document_text = '\n'.join(doc.page_texts)
        
        # 运行混合验证
        results = await pipeline.run_hybrid_validation(
            document=document_text,
            doc_obj=doc,
            use_ai_assist=use_ai_assist
        )
        
        # 转换为Issue格式
        issues = convert_results_to_issues(results)
        
        # 排序和编号
        return order_and_number_issues(issues)
        
    except Exception as e:
        logger.error(f"混合验证异步执行失败: {e}")
        # 降级到传统规则
        from .pipeline import run_rules
        return run_rules(doc, use_ai_assist=False)

def run_hybrid_rules_sync(doc, document_text: str = "", use_ai_assist: bool = False) -> List[Issue]:
    """
    同步运行混合验证规则
    
    Args:
        doc: 文档对象
        document_text: 文档文本内容
        use_ai_assist: 是否启用AI辅助
        
    Returns:
        Issue列表
    """
    try:
        # 使用asyncio运行异步函数
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                run_hybrid_rules(doc, document_text, use_ai_assist)
            )
        finally:
            loop.close()
            
    except Exception as e:
        logger.error(f"混合验证同步执行失败: {e}")
        # 降级到传统规则
        from .pipeline import run_rules
        return run_rules(doc, use_ai_assist=False)

def configure_hybrid_pipeline(
    ai_enabled: bool = True,
    rule_confidence_threshold: float = 0.8,
    ai_confidence_threshold: float = 0.7,
    merge_similarity_threshold: float = 0.8,
    merge_strategy: str = "balanced"
) -> None:
    """配置混合Pipeline"""
    global _hybrid_pipeline
    
    config = HybridConfig(
        ai_enabled=ai_enabled,
        rule_confidence_threshold=rule_confidence_threshold,
        ai_confidence_threshold=ai_confidence_threshold,
        merge_similarity_threshold=merge_similarity_threshold,
        merge_strategy=merge_strategy
    )
    
    _hybrid_pipeline = HybridPipeline(config)
    logger.info(f"混合Pipeline配置完成: AI={ai_enabled}, 合并策略={merge_strategy}")