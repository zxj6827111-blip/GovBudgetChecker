"""
混合验证架构核心定义
定义了验证问题、配置和接口的基础数据结构
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Protocol
from enum import Enum
import logging

logger = logging.getLogger(__name__)

class IssueSource(Enum):
    """问题来源"""
    RULE_ENGINE = "rule_engine"
    AI_VALIDATOR = "ai_validator"
    HYBRID = "hybrid"

class IssueSeverity(Enum):
    """问题严重程度"""
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

class IssueConfidence(Enum):
    """问题置信度"""
    LOW = 1
    MEDIUM = 2
    HIGH = 3

@dataclass
class ValidationIssue:
    """验证问题"""
    rule_id: str
    title: str
    description: str
    severity: IssueSeverity
    confidence: IssueConfidence
    source: IssueSource
    text_snippet: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = field(default_factory=dict)

@dataclass
class HybridConfig:
    """混合验证配置"""
    # 规则引擎配置
    rules_enabled: bool = True
    rules_timeout: float = 30.0
    
    # AI验证器配置
    ai_enabled: bool = True
    ai_confidence_threshold: float = 0.7
    max_ai_issues: int = 10
    ai_timeout: float = 60.0
    
    # 智能合并配置
    merge_enabled: bool = True
    similarity_threshold: float = 0.8
    max_merged_results: int = 50
    
    # 降级配置
    fallback_enabled: bool = True
    fallback_on_ai_failure: bool = True
    fallback_on_merge_failure: bool = True

class RuleAdapter(Protocol):
    """规则适配器接口"""
    
    def adapt_rules(self, document: str) -> List[ValidationIssue]:
        """适配规则引擎结果"""
        ...

class AIValidator(Protocol):
    """AI验证器接口"""
    
    async def validate_and_extend(
        self, 
        document: str, 
        rule_results: List[ValidationIssue]
    ) -> List[ValidationIssue]:
        """验证并扩展规则结果"""
        ...

class IntelligentMerger(Protocol):
    """智能合并器接口"""
    
    def merge_results(
        self,
        rule_results: List[ValidationIssue],
        ai_results: List[ValidationIssue]
    ) -> List[ValidationIssue]:
        """智能合并结果"""
        ...

@dataclass
class ValidationContext:
    """验证上下文信息"""
    document_path: str
    document_type: str
    pages_text: List[str]
    extracted_data: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)