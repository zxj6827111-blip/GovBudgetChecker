"""
规则引擎适配器 - 将现有规则系统适配到混合验证架构
"""

from typing import List, Dict, Any, Optional
import logging
from .hybrid_validator import (
    ValidationIssue, ValidationContext, IssueSource, 
    IssueSeverity, IssueConfidence
)
from .rules_v33 import ALL_RULES, Document, Issue, build_document

logger = logging.getLogger(__name__)


class RuleEngineAdapter:
    """规则引擎适配器 - 第一层验证"""
    
    def __init__(self):
        self.rules = ALL_RULES
        
    def validate(self, context: ValidationContext) -> List[ValidationIssue]:
        """执行规则验证并转换为统一格式"""
        logger.info("开始规则引擎验证...")
        
        # 1. 构建Document对象
        doc = self._build_document_from_context(context)
        
        # 2. 应用所有规则
        all_issues = []
        for rule in self.rules:
            try:
                rule_issues = rule.apply(doc)
                all_issues.extend(rule_issues)
                logger.debug(f"规则 {rule.code} 发现 {len(rule_issues)} 个问题")
            except Exception as e:
                logger.warning(f"规则 {rule.code} 执行失败: {e}")
                
        # 3. 转换为统一格式
        validation_issues = self._convert_to_validation_issues(all_issues)
        
        logger.info(f"规则引擎验证完成，发现 {len(validation_issues)} 个问题")
        return validation_issues
    
    def apply_all_rules(self, pages_text: List[str], 
                       extracted_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """兼容性方法 - 为AI验证器提供原始格式结果"""
        context = ValidationContext(
            document_path="",
            document_type="budget_report",
            pages_text=pages_text,
            extracted_data=extracted_data
        )
        
        doc = self._build_document_from_context(context)
        
        all_results = []
        for rule in self.rules:
            try:
                rule_issues = rule.apply(doc)
                for issue in rule_issues:
                    result = {
                        'rule_id': rule.code,
                        'title': f"{rule.code}: {rule.desc}",
                        'description': issue.message,
                        'severity': issue.severity,
                        'page_num': issue.location.get('page'),
                        'position': issue.location.get('pos'),
                        'text_snippet': issue.location.get('snippet', ''),
                        'expected_value': issue.location.get('expected'),
                        'actual_value': issue.location.get('actual'),
                        'metadata': issue.location
                    }
                    all_results.append(result)
            except Exception as e:
                logger.warning(f"规则 {rule.code} 执行失败: {e}")
                
        return all_results
    
    def _build_document_from_context(self, context: ValidationContext) -> Document:
        """从验证上下文构建Document对象"""
        # 从上下文提取必要信息
        page_texts = context.pages_text
        
        # 构建空的表格数据（如果没有提供）
        page_tables = context.extracted_data.get('page_tables', [])
        if not page_tables:
            page_tables = [[] for _ in page_texts]
            
        # 估算文件大小
        filesize = context.extracted_data.get('filesize', 
                                            sum(len(text.encode('utf-8')) for text in page_texts))
        
        # 使用现有的build_document函数
        doc = build_document(
            path=context.document_path,
            page_texts=page_texts,
            page_tables=page_tables,
            filesize=filesize
        )
        
        return doc
    
    def _convert_to_validation_issues(self, issues: List[Issue]) -> List[ValidationIssue]:
        """将原始Issue转换为ValidationIssue"""
        validation_issues = []
        
        for issue in issues:
            # 映射严重程度
            severity = self._map_severity(issue.severity)
            
            # 提取位置信息
            page_num = issue.location.get('page')
            position = issue.location.get('pos')
            text_snippet = issue.location.get('snippet', '')
            
            # 创建ValidationIssue
            validation_issue = ValidationIssue(
                rule_id=issue.rule,
                title=f"{issue.rule}: {self._get_rule_desc(issue.rule)}",
                description=issue.message,
                severity=severity,
                confidence=IssueConfidence.HIGH,  # 规则引擎置信度高
                source=IssueSource.RULE_ENGINE,
                page_num=page_num,
                position=position,
                text_snippet=text_snippet,
                expected_value=issue.location.get('expected'),
                actual_value=issue.location.get('actual'),
                metadata=issue.location.copy()
            )
            
            validation_issues.append(validation_issue)
            
        return validation_issues
    
    def _map_severity(self, severity: str) -> IssueSeverity:
        """映射严重程度"""
        severity_map = {
            'error': IssueSeverity.ERROR,
            'warn': IssueSeverity.WARNING,
            'warning': IssueSeverity.WARNING,
            'info': IssueSeverity.INFO
        }
        return severity_map.get(severity.lower(), IssueSeverity.WARNING)
    
    def _get_rule_desc(self, rule_code: str) -> str:
        """获取规则描述"""
        for rule in self.rules:
            if rule.code == rule_code:
                return rule.desc
        return "未知规则"
    
    def get_validator_info(self) -> Dict[str, Any]:
        """获取验证器信息"""
        return {
            "name": "RuleEngineAdapter",
            "version": "1.0",
            "description": "适配现有规则引擎到混合验证架构",
            "rules_count": len(self.rules),
            "rules": [{"code": rule.code, "desc": rule.desc, "severity": rule.severity} 
                     for rule in self.rules]
        }


class EnhancedRuleEngine:
    """增强的规则引擎 - 支持AI辅助的规则执行"""
    
    def __init__(self, ai_client=None):
        self.adapter = RuleEngineAdapter()
        self.ai_client = ai_client
        
    def validate_with_ai_assist(self, context: ValidationContext, 
                               use_ai_assist: bool = False) -> List[ValidationIssue]:
        """支持AI辅助的规则验证"""
        if not use_ai_assist or not self.ai_client:
            return self.adapter.validate(context)
            
        logger.info("开始AI辅助的规则验证...")
        
        # 1. 执行基础规则验证
        base_issues = self.adapter.validate(context)
        
        # 2. 对特定规则使用AI辅助
        enhanced_issues = []
        for issue in base_issues:
            if self._should_use_ai_assist(issue):
                enhanced_issue = self._enhance_with_ai(issue, context)
                enhanced_issues.append(enhanced_issue)
            else:
                enhanced_issues.append(issue)
                
        logger.info(f"AI辅助规则验证完成，处理了 {len(enhanced_issues)} 个问题")
        return enhanced_issues
    
    def _should_use_ai_assist(self, issue: ValidationIssue) -> bool:
        """判断是否需要AI辅助"""
        # 对复杂的文本一致性规则使用AI辅助
        ai_assist_rules = ['V33-110', 'V33-102', 'V33-103', 'V33-104', 'V33-105']
        return issue.rule_id in ai_assist_rules
    
    def _enhance_with_ai(self, issue: ValidationIssue, 
                        context: ValidationContext) -> ValidationIssue:
        """使用AI增强问题信息"""
        try:
            # 构建AI增强提示
            prompt = self._build_enhancement_prompt(issue, context)
            
            # 调用AI服务
            ai_response = self.ai_client.enhance_issue(prompt)
            
            # 更新问题信息
            if ai_response.get('enhanced_description'):
                issue.description = ai_response['enhanced_description']
                
            if ai_response.get('confidence_adjustment'):
                confidence_map = {
                    'high': IssueConfidence.HIGH,
                    'medium': IssueConfidence.MEDIUM,
                    'low': IssueConfidence.LOW
                }
                issue.confidence = confidence_map.get(
                    ai_response['confidence_adjustment'], 
                    issue.confidence
                )
                
            issue.ai_reasoning = ai_response.get('reasoning', '')
            issue.ai_suggestions = ai_response.get('suggestions', [])
            
        except Exception as e:
            logger.warning(f"AI增强失败: {e}")
            
        return issue
    
    def _build_enhancement_prompt(self, issue: ValidationIssue, 
                                context: ValidationContext) -> str:
        """构建AI增强提示"""
        return f"""
请分析并增强以下规则检测结果：

规则ID: {issue.rule_id}
问题描述: {issue.description}
文本片段: {issue.text_snippet}
页码: {issue.page_num}

请提供：
1. 更详细的问题描述
2. 置信度评估 (high/medium/low)
3. 分析理由
4. 改进建议

文档上下文：
{context.pages_text[issue.page_num-1] if issue.page_num and issue.page_num <= len(context.pages_text) else "无上下文"}
"""


def create_rule_engine_validator(ai_client=None) -> RuleEngineAdapter:
    """工厂函数：创建规则引擎验证器"""
    if ai_client:
        return EnhancedRuleEngine(ai_client).adapter
    else:
        return RuleEngineAdapter()