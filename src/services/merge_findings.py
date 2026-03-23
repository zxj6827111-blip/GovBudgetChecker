"""
结果合并服务
实现 AI 和规则引擎结果的对齐、去重和冲突检测
"""
import logging
from typing import List, Dict, Tuple, Set
from difflib import SequenceMatcher
import re

from src.schemas.issues import IssueItem, MergedSummary, ConflictItem, AnalysisConfig

logger = logging.getLogger(__name__)


class FindingsMerger:
    """结果合并器"""
    
    def __init__(self, config: AnalysisConfig):
        self.config = config
    
    def merge_findings(self, ai_findings: List[IssueItem], rule_findings: List[IssueItem]) -> MergedSummary:
        """合并两路结果"""
        logger.info(f"开始合并结果: AI={len(ai_findings)}, Rule={len(rule_findings)}")
        
        # 1. 构建相似度矩阵
        similarity_matrix = self._build_similarity_matrix(ai_findings, rule_findings)
        
        # 2. 找到匹配对
        matches, ai_unmatched, rule_unmatched = self._find_matches(
            ai_findings, rule_findings, similarity_matrix
        )
        
        # 3. 检测冲突
        conflicts = self._detect_conflicts(matches, ai_findings, rule_findings)
        
        # 4. 生成一致项
        agreements = [f"match_{i}" for i, _ in enumerate(matches) if not self._is_conflict_match(matches[i], conflicts)]
        
        # 5. 生成合并ID列表
        merged_ids = []
        for match in matches:
            ai_idx, rule_idx = match
            merged_ids.append(ai_findings[ai_idx].id)
        
        # 添加未匹配项
        for idx in ai_unmatched:
            merged_ids.append(ai_findings[idx].id)
        for idx in rule_unmatched:
            merged_ids.append(rule_findings[idx].id)
        
        # 6. 统计汇总
        totals = {
            "ai": len(ai_findings),
            "rule": len(rule_findings),
            "merged": len(merged_ids),
            "conflicts": len(conflicts),
            "agreements": len(agreements),
            "ai_only": len(ai_unmatched),
            "rule_only": len(rule_unmatched)
        }
        
        logger.info(f"合并完成: {totals}")
        
        return MergedSummary(
            totals=totals,
            conflicts=conflicts,
            agreements=agreements,
            merged_ids=merged_ids
        )
    
    def _build_similarity_matrix(self, ai_findings: List[IssueItem], rule_findings: List[IssueItem]) -> List[List[float]]:
        """构建相似度矩阵"""
        matrix = []
        for ai_item in ai_findings:
            row = []
            for rule_item in rule_findings:
                similarity = self._calculate_similarity(ai_item, rule_item)
                row.append(similarity)
            matrix.append(row)
        return matrix
    
    def _calculate_similarity(self, ai_item: IssueItem, rule_item: IssueItem) -> float:
        """计算两个问题项的相似度"""
        scores = []
        
        # 1. 标题相似度 (权重: 0.4)
        title_sim = SequenceMatcher(None, ai_item.title.lower(), rule_item.title.lower()).ratio()
        scores.append(title_sim * 0.4)
        
        # 2. 位置相似度 (权重: 0.3)
        location_sim = self._calculate_location_similarity(ai_item.location, rule_item.location)
        scores.append(location_sim * 0.3)
        
        # 3. 标签相似度 (权重: 0.2)
        tag_sim = self._calculate_tag_similarity(ai_item.tags, rule_item.tags)
        scores.append(tag_sim * 0.2)
        
        # 4. 指标相似度 (权重: 0.1)
        metrics_sim = self._calculate_metrics_similarity(ai_item.metrics, rule_item.metrics)
        scores.append(metrics_sim * 0.1)
        
        return sum(scores)
    
    def _calculate_location_similarity(self, loc1: Dict, loc2: Dict) -> float:
        """计算位置相似度"""
        if not loc1 or not loc2:
            return 0.0
        
        score = 0.0
        total_weight = 0.0
        
        # 页码相似度
        if "page" in loc1 and "page" in loc2:
            page_diff = abs(loc1["page"] - loc2["page"])
            if page_diff <= self.config.page_tolerance:
                score += 1.0 * 0.5
            total_weight += 0.5
        
        # 章节相似度
        if "section" in loc1 and "section" in loc2:
            section_sim = SequenceMatcher(None, str(loc1["section"]), str(loc2["section"])).ratio()
            score += section_sim * 0.3
            total_weight += 0.3
        
        # 表格相似度
        if "table" in loc1 and "table" in loc2:
            table_sim = SequenceMatcher(None, str(loc1["table"]), str(loc2["table"])).ratio()
            score += table_sim * 0.2
            total_weight += 0.2
        
        return score / total_weight if total_weight > 0 else 0.0
    
    def _calculate_tag_similarity(self, tags1: List[str], tags2: List[str]) -> float:
        """计算标签相似度"""
        if not tags1 or not tags2:
            return 0.0
        
        set1 = set(tag.lower() for tag in tags1)
        set2 = set(tag.lower() for tag in tags2)
        
        intersection = len(set1 & set2)
        union = len(set1 | set2)
        
        return intersection / union if union > 0 else 0.0
    
    def _calculate_metrics_similarity(self, metrics1: Dict, metrics2: Dict) -> float:
        """计算指标相似度"""
        if not metrics1 or not metrics2:
            return 0.0
        
        common_keys = set(metrics1.keys()) & set(metrics2.keys())
        if not common_keys:
            return 0.0
        
        similarities = []
        for key in common_keys:
            val1, val2 = metrics1[key], metrics2[key]
            
            # 数值比较
            if isinstance(val1, (int, float)) and isinstance(val2, (int, float)):
                if val1 == 0 and val2 == 0:
                    similarities.append(1.0)
                elif val1 == 0 or val2 == 0:
                    similarities.append(0.0)
                else:
                    rel_diff = abs(val1 - val2) / max(abs(val1), abs(val2))
                    if rel_diff <= self.config.money_tolerance:
                        similarities.append(1.0)
                    else:
                        similarities.append(max(0.0, 1.0 - rel_diff))
            # 字符串比较
            elif isinstance(val1, str) and isinstance(val2, str):
                similarities.append(SequenceMatcher(None, val1.lower(), val2.lower()).ratio())
            else:
                similarities.append(1.0 if val1 == val2 else 0.0)
        
        return sum(similarities) / len(similarities) if similarities else 0.0
    
    def _find_matches(self, ai_findings: List[IssueItem], rule_findings: List[IssueItem], 
                     similarity_matrix: List[List[float]]) -> Tuple[List[Tuple[int, int]], List[int], List[int]]:
        """找到匹配对"""
        matches = []
        ai_matched = set()
        rule_matched = set()
        
        # 贪心匹配：按相似度从高到低
        candidates = []
        for i, row in enumerate(similarity_matrix):
            for j, sim in enumerate(row):
                if sim >= self.config.title_similarity_threshold:
                    candidates.append((sim, i, j))
        
        candidates.sort(reverse=True)  # 按相似度降序
        
        for sim, ai_idx, rule_idx in candidates:
            if ai_idx not in ai_matched and rule_idx not in rule_matched:
                matches.append((ai_idx, rule_idx))
                ai_matched.add(ai_idx)
                rule_matched.add(rule_idx)
        
        ai_unmatched = [i for i in range(len(ai_findings)) if i not in ai_matched]
        rule_unmatched = [j for j in range(len(rule_findings)) if j not in rule_matched]
        
        return matches, ai_unmatched, rule_unmatched
    
    def _detect_conflicts(self, matches: List[Tuple[int, int]], 
                         ai_findings: List[IssueItem], rule_findings: List[IssueItem]) -> List[ConflictItem]:
        """检测冲突"""
        conflicts = []
        
        for match_idx, (ai_idx, rule_idx) in enumerate(matches):
            ai_item = ai_findings[ai_idx]
            rule_item = rule_findings[rule_idx]
            
            conflict = self._check_item_conflict(ai_item, rule_item, f"match_{match_idx}")
            if conflict:
                conflicts.append(conflict)
        
        return conflicts
    
    def _check_item_conflict(self, ai_item: IssueItem, rule_item: IssueItem, key: str) -> ConflictItem:
        """检查单个匹配对是否冲突"""
        conflicts = []
        details = {}
        
        # 1. 严重程度冲突
        if ai_item.severity != rule_item.severity:
            severity_levels = {"info": 1, "low": 2, "manual_review": 2.5, "medium": 3, "high": 4, "critical": 5}
            ai_level = severity_levels.get(ai_item.severity, 0)
            rule_level = severity_levels.get(rule_item.severity, 0)
            
            if abs(ai_level - rule_level) >= 2:  # 相差2级以上认为是冲突
                conflicts.append("severity-mismatch")
                details["severity"] = {
                    "ai": ai_item.severity,
                    "rule": rule_item.severity
                }
        
        # 2. 指标值冲突
        metrics_conflict = self._check_metrics_conflict(ai_item.metrics, rule_item.metrics)
        if metrics_conflict:
            conflicts.append("value-mismatch")
            details["metrics"] = metrics_conflict
        
        # 3. 页码冲突
        ai_page = ai_item.location.get("page", 0)
        rule_page = rule_item.location.get("page", 0)
        if ai_page > 0 and rule_page > 0 and abs(ai_page - rule_page) > self.config.page_tolerance:
            conflicts.append("page-mismatch")
            details["page"] = {
                "ai": ai_page,
                "rule": rule_page
            }
        
        if conflicts:
            return ConflictItem(
                key=key,
                ai_issue=ai_item.id,
                rule_issue=rule_item.id,
                reason=conflicts[0],  # 取第一个冲突原因
                details=details
            )
        
        return None
    
    def _check_metrics_conflict(self, metrics1: Dict, metrics2: Dict) -> Dict:
        """检查指标冲突"""
        conflicts = {}
        
        common_keys = set(metrics1.keys()) & set(metrics2.keys())
        for key in common_keys:
            val1, val2 = metrics1[key], metrics2[key]
            
            if isinstance(val1, (int, float)) and isinstance(val2, (int, float)):
                if val1 != 0 and val2 != 0:
                    rel_diff = abs(val1 - val2) / max(abs(val1), abs(val2))
                    if rel_diff > self.config.money_tolerance:
                        conflicts[key] = {
                            "ai": val1,
                            "rule": val2,
                            "diff_pct": rel_diff * 100
                        }
        
        return conflicts
    
    def _is_conflict_match(self, match: Tuple[int, int], conflicts: List[ConflictItem]) -> bool:
        """判断匹配对是否有冲突"""
        match_key = f"match_{match[0]}_{match[1]}"
        return any(conflict.key == match_key for conflict in conflicts)


def merge_findings(ai_findings: List[IssueItem], rule_findings: List[IssueItem], 
                  config: AnalysisConfig) -> MergedSummary:
    """合并结果的便捷函数"""
    merger = FindingsMerger(config)
    return merger.merge_findings(ai_findings, rule_findings)
