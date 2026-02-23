"""
组织匹配服务
根据 PDF 文件名和内容自动匹配组织
"""
import re
import logging
from typing import Optional, Tuple, List
from schemas.organization import Organization
from services.org_storage import get_org_storage

logger = logging.getLogger(__name__)


class OrgMatcher:
    """组织匹配器"""
    
    def __init__(self):
        self.storage = get_org_storage()
    
    def match(self, filename: str, first_page_text: str = "") -> Tuple[Optional[Organization], float]:
        """
        尝试匹配组织
        
        Args:
            filename: PDF 文件名
            first_page_text: 第一页文本内容
            
        Returns:
            (匹配到的组织, 置信度) 或 (None, 0) 表示未匹配
        """
        # 合并文件名和首页文本作为匹配源
        search_text = f"{filename} {first_page_text}"
        
        # 清理文本
        search_text = self._clean_text(search_text)
        
        best_match: Optional[Organization] = None
        best_score = 0.0
        
        organizations = self.storage.get_all()
        
        # 收集所有候选匹配及其分数
        candidates = []
        for org in organizations:
            score = self._calculate_match_score(org, search_text)
            if score > 0:
                candidates.append((org, score))
        
        # 按分数降序排序，分数相同时按名称长度降序（优先最具体的）
        candidates.sort(key=lambda x: (x[1], len(x[0].name)), reverse=True)
        
        if candidates:
            best_match, best_score = candidates[0]
        
        # 置信度阈值
        if best_score >= 0.6:
            logger.info(f"Matched '{filename}' to '{best_match.name}' with confidence {best_score:.2f}")
            return best_match, best_score
        
        logger.info(f"No confident match for '{filename}' (best score: {best_score:.2f})")
        return None, 0
    
    def _clean_text(self, text: str) -> str:
        """清理文本"""
        # 移除常见的无关词
        noise_words = [
            "2024", "2023", "2022", "2021", "2020",
            "年度", "部门", "决算", "预算", "公开",
            ".pdf", ".PDF"
        ]
        for word in noise_words:
            text = text.replace(word, " ")
        
        # 移除多余空格
        text = re.sub(r'\s+', ' ', text)
        return text.strip()
    
    def _calculate_match_score(self, org: Organization, search_text: str) -> float:
        """计算匹配分数"""
        score = 0.0
        
        # 1. 精确匹配组织名称
        if org.name in search_text:
            score = max(score, 0.9)
        
        # 2. 匹配关键词
        for keyword in org.keywords:
            if keyword in search_text:
                score = max(score, 0.8)
        
        # 3. 部分匹配（去除常见后缀）
        name_core = self._extract_core_name(org.name)
        if name_core and name_core in search_text:
            score = max(score, 0.7)
        
        # 4. 模糊匹配（至少3个连续字符匹配）
        if self._fuzzy_match(org.name, search_text):
            score = max(score, 0.5)
        
        return score
    
    def _extract_core_name(self, name: str) -> str:
        """提取核心名称（去除常见后缀）"""
        suffixes = ["局", "委", "办", "中心", "站", "所", "院", "会"]
        core = name
        for suffix in suffixes:
            if core.endswith(suffix) and len(core) > len(suffix):
                core = core[:-len(suffix)]
        return core
    
    def _fuzzy_match(self, name: str, text: str, min_chars: int = 3) -> bool:
        """模糊匹配：检查是否有足够长的连续子串匹配"""
        for i in range(len(name) - min_chars + 1):
            substr = name[i:i + min_chars]
            if substr in text:
                return True
        return False
    
    def suggest_matches(self, filename: str, first_page_text: str = "", top_n: int = 5) -> List[Tuple[Organization, float]]:
        """
        返回多个可能的匹配建议
        
        Returns:
            [(组织, 置信度), ...] 按置信度降序排列
        """
        search_text = f"{filename} {first_page_text}"
        search_text = self._clean_text(search_text)
        
        organizations = self.storage.get_all()
        matches = []
        
        for org in organizations:
            score = self._calculate_match_score(org, search_text)
            if score > 0:
                matches.append((org, score))
        
        # 按分数降序排列
        matches.sort(key=lambda x: x[1], reverse=True)
        return matches[:top_n]


# 单例实例
_matcher_instance = None

def get_org_matcher() -> OrgMatcher:
    """获取组织匹配器单例"""
    global _matcher_instance
    if _matcher_instance is None:
        _matcher_instance = OrgMatcher()
    return _matcher_instance
