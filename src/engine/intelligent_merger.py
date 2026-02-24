"""
智能合并算法实现
负责合并规则引擎和AI验证器的结果，去重并优化输出
"""

import logging
from typing import List, Dict, Any, Optional, Set, Tuple
from dataclasses import dataclass, field
from enum import Enum
import hashlib
from collections import defaultdict

from .hybrid_validator import ValidationIssue, IssueSource, IssueSeverity, IssueConfidence

logger = logging.getLogger(__name__)

class MergeStrategy(Enum):
    """合并策略"""
    CONSERVATIVE = "conservative"  # 保守：保留更多问题
    AGGRESSIVE = "aggressive"      # 激进：去重更多问题
    BALANCED = "balanced"          # 平衡：默认策略
    RULE_PRIORITY = "rule_priority"  # 规则优先：规则结果优先级更高

@dataclass
class MergeConfig:
    """合并配置"""
    similarity_threshold: float = 0.8
    strategy: MergeStrategy = MergeStrategy.BALANCED
    max_results: int = 50
    boost_confidence: bool = True
    remove_duplicates: bool = True

@dataclass
class MergeCandidate:
    """合并候选项"""
    rule_result: Optional[ValidationIssue] = None
    ai_results: List[ValidationIssue] = field(default_factory=list)
    similarity_score: float = 0.0
    merge_reason: str = ""

class SmartIntelligentMerger:
    """智能合并器实现"""
    
    def __init__(self, config: Optional[MergeConfig] = None):
        self.config = config or MergeConfig()
        
    def merge_results(
        self,
        rule_results: List[ValidationIssue],
        ai_results: List[ValidationIssue]
    ) -> List[ValidationIssue]:
        """
        合并规则引擎和AI验证器的结果
        
        Args:
            rule_results: 规则引擎结果
            ai_results: AI验证器结果
            
        Returns:
            合并后的结果列表
        """
        try:
            logger.info(f"开始合并结果: 规则{len(rule_results)}个, AI{len(ai_results)}个")
            
            # 第一步：预处理
            rule_results = self._preprocess_results(rule_results, IssueSource.RULE_ENGINE)
            ai_results = self._preprocess_results(ai_results, IssueSource.AI_VALIDATOR)
            
            # 第二步：找到合并候选项
            candidates = self._find_merge_candidates(rule_results, ai_results)
            
            # 第三步：执行合并
            merged_results = self._execute_merge(candidates)
            
            # 第四步：后处理
            final_results = self._postprocess_results(merged_results)
            
            logger.info(f"合并完成: 最终{len(final_results)}个问题")
            return final_results
            
        except Exception as e:
            logger.error(f"合并过程出错: {e}")
            # 出错时简单合并
            return rule_results + ai_results
    
    def _preprocess_results(
        self, 
        results: List[ValidationIssue],
        source: IssueSource
    ) -> List[ValidationIssue]:
        """预处理结果：标准化和过滤"""
        processed = []
        for result in results:
            # 确保来源正确
            if result.source != source:
                result.source = source
            
            # 过滤低质量结果
            if self._is_valid_result(result):
                processed.append(result)
        
        return processed
    
    def _find_merge_candidates(
        self,
        rule_results: List[ValidationIssue],
        ai_results: List[ValidationIssue]
    ) -> List[MergeCandidate]:
        """找到合并候选项"""
        candidates = []
        used_ai_indices = set()
        
        # 为每个规则结果找匹配的AI结果
        for rule_result in rule_results:
            candidate = MergeCandidate(rule_result=rule_result)
            
            for i, ai_result in enumerate(ai_results):
                if i in used_ai_indices:
                    continue
                
                similarity = self._calculate_similarity(rule_result, ai_result)
                if similarity >= self.config.similarity_threshold:
                    candidate.ai_results.append(ai_result)
                    candidate.similarity_score = max(candidate.similarity_score, similarity)
                    used_ai_indices.add(i)
            
            candidates.append(candidate)
        
        # 添加未匹配的AI结果
        for i, ai_result in enumerate(ai_results):
            if i not in used_ai_indices:
                candidate = MergeCandidate(ai_results=[ai_result])
                candidates.append(candidate)
        
        return candidates
    
    def _calculate_similarity(
        self,
        result1: ValidationIssue,
        result2: ValidationIssue
    ) -> float:
        """计算两个结果的相似度"""
        try:
            # 1. 规则ID相似度
            rule_sim = 1.0 if result1.rule_id == result2.rule_id else 0.0
            
            # 2. 描述相似度（简化版）
            desc1 = (result1.description or "").lower()
            desc2 = (result2.description or "").lower()
            
            if not desc1 or not desc2:
                desc_sim = 0.0
            else:
                # 简单的词汇重叠计算
                words1 = set(desc1.split())
                words2 = set(desc2.split())
                if words1 or words2:
                    desc_sim = len(words1 & words2) / len(words1 | words2)
                else:
                    desc_sim = 0.0
            
            # 3. 文本片段相似度
            text1 = (result1.text_snippet or "").lower()
            text2 = (result2.text_snippet or "").lower()
            
            if text1 and text2:
                # 检查是否有重叠内容
                text_sim = 1.0 if text1 in text2 or text2 in text1 else 0.0
            else:
                text_sim = 0.0
            
            # 加权平均
            total_sim = (rule_sim * 0.4 + desc_sim * 0.4 + text_sim * 0.2)
            
            return min(1.0, total_sim)
            
        except Exception as e:
            logger.warning(f"计算相似度时出错: {e}")
            return 0.0
    
    def _execute_merge(self, candidates: List[MergeCandidate]) -> List[ValidationIssue]:
        """执行合并"""
        merged_results = []
        
        for candidate in candidates:
            if candidate.rule_result and candidate.ai_results:
                # 规则+AI合并
                merged = self._merge_rule_and_ai(candidate)
                merged_results.append(merged)
            elif candidate.rule_result:
                # 只有规则结果
                merged_results.append(candidate.rule_result)
            elif candidate.ai_results:
                # 只有AI结果
                if len(candidate.ai_results) == 1:
                    merged_results.append(candidate.ai_results[0])
                else:
                    # 多个AI结果合并
                    merged = self._merge_multiple_ai(candidate.ai_results)
                    merged_results.append(merged)
        
        return merged_results
    
    def _merge_rule_and_ai(self, candidate: MergeCandidate) -> ValidationIssue:
        """合并规则和AI结果"""
        rule_result = candidate.rule_result
        ai_results = candidate.ai_results
        
        # 基于规则结果创建合并结果
        merged = self._copy_result(rule_result)
        
        # 增强描述（如果AI提供了更详细的描述）
        ai_descriptions = [ai.description for ai in ai_results if ai.description]
        if ai_descriptions:
            # 选择最长的描述作为增强
            best_desc = max(ai_descriptions, key=len)
            if len(best_desc) > len(merged.description or ""):
                merged.description = best_desc
        
        # 提升置信度
        if self.config.boost_confidence:
            merged.confidence = self._boost_confidence(merged.confidence)
        
        # 合并元数据
        merged.metadata = merged.metadata or {}
        merged.metadata["merged_from"] = "rule_and_ai"
        merged.metadata["ai_confirmations"] = len(ai_results)
        merged.metadata["similarity_score"] = candidate.similarity_score
        
        return merged
    
    def _merge_multiple_ai(self, ai_results: List[ValidationIssue]) -> ValidationIssue:
        """合并多个AI结果"""
        if not ai_results:
            raise ValueError("AI结果列表不能为空")
        
        # 选择置信度最高的作为基础
        base_result = max(ai_results, key=lambda x: x.confidence.value)
        merged = self._copy_result(base_result)
        
        # 合并元数据
        merged.metadata = merged.metadata or {}
        merged.metadata["merged_from"] = "multiple_ai"
        merged.metadata["ai_count"] = len(ai_results)
        
        return merged
    
    def _postprocess_results(
        self,
        results: List[ValidationIssue]
    ) -> List[ValidationIssue]:
        """后处理结果"""
        # 去重
        if self.config.remove_duplicates:
            results = self._remove_duplicates(results)
        
        # 限制数量
        if len(results) > self.config.max_results:
            # 按严重程度和置信度排序
            results.sort(key=lambda x: (x.severity.value, x.confidence.value), reverse=True)
            results = results[:self.config.max_results]
        
        return results
    
    def _remove_duplicates(self, results: List[ValidationIssue]) -> List[ValidationIssue]:
        """去重"""
        seen_hashes = set()
        unique_results = []
        
        for result in results:
            # 创建结果的哈希
            content = f"{result.rule_id}:{result.description}:{result.text_snippet}"
            result_hash = hashlib.md5(content.encode()).hexdigest()
            
            if result_hash not in seen_hashes:
                seen_hashes.add(result_hash)
                unique_results.append(result)
        
        return unique_results
    
    def _copy_result(self, result: ValidationIssue) -> ValidationIssue:
        """复制结果"""
        return ValidationIssue(
            rule_id=result.rule_id,
            title=result.title,
            description=result.description,
            severity=result.severity,
            confidence=result.confidence,
            source=result.source,
            text_snippet=result.text_snippet,
            metadata=dict(result.metadata) if result.metadata else {}
        )
    
    def _boost_confidence(self, confidence: IssueConfidence) -> IssueConfidence:
        """提升置信度"""
        if confidence == IssueConfidence.LOW:
            return IssueConfidence.MEDIUM
        elif confidence == IssueConfidence.MEDIUM:
            return IssueConfidence.HIGH
        else:
            return confidence
    
    def _is_valid_result(self, result: ValidationIssue) -> bool:
        """检查结果是否有效"""
        return (
            result.rule_id and 
            result.description and 
            len(result.description.strip()) > 5
        )

def create_intelligent_merger(config: Optional[MergeConfig] = None) -> SmartIntelligentMerger:
    """创建智能合并器实例"""
    return SmartIntelligentMerger(config)