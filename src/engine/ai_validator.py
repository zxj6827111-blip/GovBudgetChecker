"""
AI验证器实现
负责使用AI技术验证和增强规则引擎的结果
"""

import logging
from typing import List, Dict, Any, Optional, Set, Tuple
from dataclasses import dataclass, field
import asyncio
from enum import Enum
import hashlib

from .hybrid_validator import ValidationIssue, IssueSource, IssueSeverity, IssueConfidence
from .ai.extractor_client import ExtractorClient, ExtractorConfig

logger = logging.getLogger(__name__)

class ValidationAction(Enum):
    """验证动作"""
    CONFIRM = "confirm"      # 确认规则发现的问题
    REJECT = "reject"        # 拒绝规则发现的问题
    ENHANCE = "enhance"      # 增强问题描述
    DISCOVER = "discover"    # 发现新问题

@dataclass
class AIValidationConfig:
    """AI验证配置"""
    enabled: bool = True
    confidence_threshold: float = 0.7
    max_new_issues: int = 10
    validation_timeout: float = 60.0
    extractor_config: Optional[ExtractorConfig] = None
    
    # 功能开关
    validate_rule_results: bool = True      # 是否验证规则结果
    discover_new_issues: bool = True        # 是否发现新问题
    enhance_descriptions: bool = True       # 是否增强问题描述
    cross_validate: bool = True             # 是否交叉验证

@dataclass
class ValidationContext:
    """验证上下文"""
    document_text: str
    document_hash: str
    section_name: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

class SmartAIValidator:
    """智能AI验证器实现"""
    
    def __init__(self, config: Optional[AIValidationConfig] = None):
        self.config = config or AIValidationConfig()
        self.extractor_client = ExtractorClient(self.config.extractor_config)
        
        # 问题来源映射
        self.source_mapping = {
            "budget_format": IssueSource.AI_VALIDATOR,
            "calculation_error": IssueSource.AI_VALIDATOR,
            "compliance_issue": IssueSource.AI_VALIDATOR,
            "data_inconsistency": IssueSource.AI_VALIDATOR,
        }

    async def validate_and_extend(
        self, 
        document: str, 
        rule_results: List[ValidationIssue],
        context: Optional[ValidationContext] = None
    ) -> List[ValidationIssue]:
        """
        验证并扩展规则结果
        
        Args:
            document: 文档内容
            rule_results: 规则引擎结果
            context: 验证上下文
            
        Returns:
            验证和扩展后的结果列表
        """
        if not self.config.enabled:
            return rule_results
        
        try:
            # 创建验证上下文
            if context is None:
                context = ValidationContext(
                    document_text=document,
                    document_hash=self._generate_hash(document)
                )
            
            logger.info(f"开始AI验证: {len(rule_results)}个规则结果")
            
            # 第一步：验证规则结果
            validated_results = rule_results
            if self.config.validate_rule_results:
                validated_results = await self._validate_rule_results(rule_results, context)
            
            # 第二步：发现新问题
            new_issues = []
            if self.config.discover_new_issues:
                new_issues = await self._discover_new_issues(context, validated_results)
            
            # 第三步：增强描述
            all_results = validated_results + new_issues
            if self.config.enhance_descriptions:
                all_results = await self._enhance_descriptions(all_results, context)
            
            logger.info(f"AI验证完成: 验证{len(validated_results)}个, 新发现{len(new_issues)}个")
            return all_results
            
        except Exception as e:
            logger.error(f"AI验证过程出错: {e}")
            return rule_results
    
    async def _validate_rule_results(
        self, 
        rule_results: List[ValidationIssue], 
        context: ValidationContext
    ) -> List[ValidationIssue]:
        """验证规则结果"""
        validated_results = []
        
        for result in rule_results:
            try:
                # 验证单个结果
                action = await self._validate_single_result(result, context)
                
                if action == ValidationAction.CONFIRM:
                    # 确认结果，可能提升置信度
                    if result.confidence == IssueConfidence.LOW:
                        result.confidence = IssueConfidence.MEDIUM
                    validated_results.append(result)
                elif action == ValidationAction.ENHANCE:
                    # 增强结果
                    enhanced_result = await self._enhance_single_result(result, context)
                    validated_results.append(enhanced_result)
                elif action == ValidationAction.REJECT:
                    # 拒绝结果，记录日志但不添加
                    logger.info(f"AI拒绝规则结果: {result.rule_id}")
                else:
                    # 默认保留
                    validated_results.append(result)
                    
            except Exception as e:
                logger.warning(f"验证单个结果时出错: {e}")
                # 出错时保留原结果
                validated_results.append(result)
        
        return validated_results
    
    async def _validate_single_result(
        self, 
        result: ValidationIssue, 
        context: ValidationContext
    ) -> ValidationAction:
        """验证单个规则结果"""
        try:
            # 构造验证提示
            validation_prompt = self._build_validation_prompt(result, context)
            
            # 调用AI进行验证
            ai_response = await self._call_ai_for_validation(validation_prompt, context)
            
            # 解析AI响应
            return self._parse_validation_response(ai_response)
            
        except Exception as e:
            logger.warning(f"验证单个结果时出错: {e}")
            return ValidationAction.CONFIRM  # 默认确认
    
    async def _discover_new_issues(
        self, 
        context: ValidationContext, 
        existing_results: List[ValidationIssue]
    ) -> List[ValidationIssue]:
        """发现新问题"""
        try:
            # 使用AI抽取器发现新问题
            ai_hits = await self.extractor_client.ai_extract_pairs(
                context.document_text, 
                context.document_hash
            )
            
            new_issues = []
            existing_issues_set = self._build_existing_issues_set(existing_results)
            
            for hit in ai_hits[:self.config.max_new_issues]:
                # 检查是否为新问题
                if not self._is_duplicate_issue(hit, existing_issues_set):
                    new_issue = self._convert_ai_hit_to_result(hit, context)
                    if new_issue and new_issue.confidence >= IssueConfidence.MEDIUM:
                        new_issues.append(new_issue)
            
            logger.info(f"AI发现{len(new_issues)}个新问题")
            return new_issues
            
        except Exception as e:
            logger.error(f"发现新问题时出错: {e}")
            return []
    
    async def _enhance_descriptions(
        self, 
        results: List[ValidationIssue], 
        context: ValidationContext
    ) -> List[ValidationIssue]:
        """增强问题描述"""
        enhanced_results = []
        
        for result in results:
            try:
                if result.confidence >= IssueConfidence.MEDIUM:
                    enhanced_result = await self._enhance_single_result(result, context)
                    enhanced_results.append(enhanced_result)
                else:
                    enhanced_results.append(result)
            except Exception as e:
                logger.warning(f"增强描述时出错: {e}")
                enhanced_results.append(result)
        
        return enhanced_results
    
    async def _enhance_single_result(
        self, 
        result: ValidationIssue, 
        context: ValidationContext
    ) -> ValidationIssue:
        """增强单个结果的描述"""
        try:
            # 构造增强提示
            enhancement_prompt = self._build_enhancement_prompt(result, context)
            
            # 调用AI进行增强
            ai_response = await self._call_ai_for_enhancement(enhancement_prompt, context)
            
            # 解析并应用增强
            enhanced_description = self._parse_enhancement_response(ai_response)
            if enhanced_description:
                result.description = enhanced_description
                result.metadata = result.metadata or {}
                result.metadata["ai_enhanced"] = True
            
            return result
            
        except Exception as e:
            logger.warning(f"增强单个结果时出错: {e}")
            return result
    
    def _build_validation_prompt(self, result: ValidationIssue, context: ValidationContext) -> str:
        """构建验证提示"""
        return f"""
请验证以下预算检查问题是否准确：

问题描述：{result.description}
规则ID：{result.rule_id}
文本片段：{result.text_snippet or '无'}

文档上下文：
{context.document_text[:500]}...

请回答：CONFIRM（确认）、REJECT（拒绝）或 ENHANCE（需要增强）
"""
    
    def _build_enhancement_prompt(self, result: ValidationIssue, context: ValidationContext) -> str:
        """构建增强提示"""
        return f"""
请为以下预算检查问题提供更详细的描述：

当前描述：{result.description}
规则ID：{result.rule_id}
文本片段：{result.text_snippet or '无'}

文档上下文：
{context.document_text[:500]}...

请提供更详细、更准确的问题描述：
"""
    
    async def _call_ai_for_validation(self, prompt: str, context: ValidationContext) -> str:
        """调用AI进行验证"""
        try:
            # 这里应该调用实际的AI服务
            # 暂时返回模拟响应
            return "CONFIRM"
        except Exception as e:
            logger.error(f"调用AI验证服务失败: {e}")
            return "CONFIRM"
    
    async def _call_ai_for_enhancement(self, prompt: str, context: ValidationContext) -> str:
        """调用AI进行增强"""
        try:
            # 这里应该调用实际的AI服务
            # 暂时返回模拟响应
            return ""
        except Exception as e:
            logger.error(f"调用AI增强服务失败: {e}")
            return ""
    
    def _parse_validation_response(self, response: str) -> ValidationAction:
        """解析验证响应"""
        response = response.strip().upper()
        if "CONFIRM" in response:
            return ValidationAction.CONFIRM
        elif "REJECT" in response:
            return ValidationAction.REJECT
        elif "ENHANCE" in response:
            return ValidationAction.ENHANCE
        else:
            return ValidationAction.CONFIRM
    
    def _parse_enhancement_response(self, response: str) -> Optional[str]:
        """解析增强响应"""
        if response and len(response.strip()) > 10:
            return response.strip()
        return None
    
    def _build_existing_issues_set(self, results: List[ValidationIssue]) -> Set[str]:
        """构建已存在问题的集合"""
        existing_set = set()
        for result in results:
            # 使用描述和文本片段的组合作为唯一标识
            key = f"{result.description}:{result.text_snippet or ''}"
            existing_set.add(key.lower())
        return existing_set
    
    def _is_duplicate_issue(self, ai_hit: Dict[str, Any], existing_set: Set[str]) -> bool:
        """检查是否为重复问题"""
        description = ai_hit.get("description", "")
        text_snippet = ai_hit.get("text_snippet", "")
        key = f"{description}:{text_snippet}".lower()
        return key in existing_set
    
    def _convert_ai_hit_to_result(
        self, 
        hit: Dict[str, Any], 
        context: ValidationContext
    ) -> Optional[ValidationIssue]:
        """将AI发现转换为验证结果"""
        try:
            return ValidationIssue(
                rule_id=hit.get("rule_id", "AI_DISCOVERED"),
                title=hit.get("title", "AI发现的问题"),
                description=hit.get("description", ""),
                severity=self._determine_severity(hit),
                confidence=self._determine_confidence(hit),
                source=IssueSource.AI_VALIDATOR,
                text_snippet=hit.get("text_snippet", ""),
                metadata={
                    "ai_discovered": True,
                    "confidence_score": hit.get("confidence_score", 0.0),
                    "discovery_method": "ai_extraction"
                }
            )
        except Exception as e:
            logger.warning(f"转换AI发现时出错: {e}")
            return None
    
    def _determine_severity(self, hit: Dict[str, Any]) -> IssueSeverity:
        """确定问题严重程度"""
        severity_score = hit.get("severity_score", 0.5)
        if severity_score >= 0.8:
            return IssueSeverity.HIGH
        elif severity_score >= 0.5:
            return IssueSeverity.MEDIUM
        else:
            return IssueSeverity.LOW
    
    def _determine_confidence(self, hit: Dict[str, Any]) -> IssueConfidence:
        """确定置信度"""
        confidence_score = hit.get("confidence_score", 0.5)
        if confidence_score >= 0.8:
            return IssueConfidence.HIGH
        elif confidence_score >= 0.6:
            return IssueConfidence.MEDIUM
        else:
            return IssueConfidence.LOW
    
    def _generate_hash(self, text: str) -> str:
        """生成文本哈希"""
        return hashlib.md5(text.encode()).hexdigest()

def create_ai_validator(config: Optional[AIValidationConfig] = None) -> SmartAIValidator:
    """创建AI验证器实例"""
    return SmartAIValidator(config)