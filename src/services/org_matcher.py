"""
组织匹配服务
根据 PDF 文件名和内容自动匹配组织
"""
import re
import logging
from typing import Optional, Tuple, List, Set
from src.schemas.organization import Organization
from src.services.org_storage import get_org_storage

logger = logging.getLogger(__name__)

_GENERIC_VARIANTS = {
    "上海市",
    "普陀区",
    "人民政府",
    "办公室",
    "办事处",
    "委员会",
    "本部",
    "本级",
}
_ADMIN_PREFIXES = (
    "上海市普陀区",
    "上海市",
    "普陀区",
    "上海",
    "人民政府",
    "中国共产党",
    "中共",
)


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
        raw_search_text = f"{filename} {first_page_text}"
        
        # 清理文本
        search_text = self._clean_text(raw_search_text)
        
        best_match: Optional[Organization] = None
        best_score = 0.0
        
        organizations = self.storage.get_all()
        
        # 收集所有候选匹配及其分数
        candidates = []
        for org in organizations:
            score = self._calculate_match_score(org, search_text, raw_search_text=raw_search_text)
            if score > 0:
                candidates.append((org, score))
        
        # 按分数降序排序，分数相同时按名称长度降序（优先最具体的）
        candidates.sort(key=lambda x: (x[1], len(x[0].name)), reverse=True)
        
        if candidates:
            best_match, best_score = candidates[0]

        if (
            best_match is not None
            and best_match.level in {"city", "district"}
            and self._contains_specific_entity_cue(raw_search_text)
        ):
            logger.info(
                "Rejecting generic %s-level match for '%s' because filename indicates a more specific entity",
                best_match.level,
                filename,
            )
            return None, 0

        # 置信度阈值
        if best_score >= 0.6:
            logger.info(f"Matched '{filename}' to '{best_match.name}' with confidence {best_score:.2f}")
            return best_match, best_score
        
        logger.info(f"No confident match for '{filename}' (best score: {best_score:.2f})")
        return None, 0
    
    def _clean_text(self, text: str) -> str:
        """清理文本"""
        text = re.sub(r"20\d{2}(?:年度|年)?", " ", text)
        text = re.sub(r"\d{2}(?:年度|年)", " ", text)

        # 移除常见的无关词
        noise_words = [
            "年度", "决算", "预算", "公开",
            ".pdf", ".PDF"
        ]
        for word in noise_words:
            text = text.replace(word, " ")
        
        # 移除多余空格
        text = re.sub(r'\s+', ' ', text)
        return text.strip()
    
    def _calculate_match_score(
        self,
        org: Organization,
        search_text: str,
        raw_search_text: str = "",
    ) -> float:
        """计算匹配分数"""
        score = 0.0
        normalized_search = self._normalize_match_text(search_text)
        normalized_name = self._normalize_match_text(org.name)
        
        # 1. 精确匹配组织名称
        if normalized_name and normalized_name in normalized_search:
            score = max(score, self._full_name_match_score(org))

        # 2. 名称变体匹配（本部/本级、人民政府、办事处等别名）
        for variant in self._build_name_variants(org.name):
            if variant == normalized_name:
                continue
            if variant and variant in normalized_search:
                score = max(score, self._variant_match_score(org, variant, normalized_name))
        
        # 3. 匹配关键词
        for keyword in org.keywords:
            keyword_norm = self._normalize_match_text(keyword)
            if keyword_norm and keyword_norm in normalized_search:
                score = max(score, self._keyword_match_score(org, keyword_norm, normalized_name))
        
        # 4. 部分匹配（去除常见后缀）
        name_core = self._normalize_match_text(self._extract_core_name(org.name))
        if name_core and name_core in normalized_search:
            score = max(score, self._variant_match_score(org, name_core, normalized_name))
        
        # 5. 模糊匹配（仅针对去掉行政前缀后的核心名称，避免“上海市/普陀区”误伤）
        if self._fuzzy_match(org.name, search_text):
            score = max(score, 0.5)

        score = self._apply_scope_hint(score, org, raw_search_text or search_text)
        
        return min(score, 0.99)

    def _apply_scope_hint(self, score: float, org: Organization, search_text: str) -> float:
        text = str(search_text or "")
        hinted_score = score
        if "单位" in text or "本级" in text or "区级单位" in text:
            if org.level == "unit":
                hinted_score += 0.12
        elif "部门" in text:
            if org.level == "department":
                hinted_score += 0.08
        return min(hinted_score, 0.99)

    def _full_name_match_score(self, org: Organization) -> float:
        if org.level in {"department", "unit"}:
            return 0.96
        return 0.78

    def _variant_match_score(self, org: Organization, variant: str, normalized_name: str) -> float:
        if self._is_generic_phrase(variant):
            return 0.34 if org.level in {"department", "unit"} else 0.28

        name_length = len(normalized_name) or len(variant) or 1
        coverage = len(variant) / name_length
        score = 0.76 if org.level in {"department", "unit"} else 0.62
        if coverage >= 0.85:
            score += 0.12
        elif coverage >= 0.65:
            score += 0.08
        elif coverage >= 0.45:
            score += 0.03
        if self._is_specific_phrase(variant):
            score += 0.04
        return min(score, 0.94)

    def _keyword_match_score(self, org: Organization, keyword: str, normalized_name: str) -> float:
        if self._is_generic_phrase(keyword):
            return 0.32

        name_length = len(normalized_name) or len(keyword) or 1
        coverage = len(keyword) / name_length
        score = 0.68 if org.level in {"department", "unit"} else 0.58
        if coverage >= 0.85:
            score += 0.16
        elif coverage >= 0.65:
            score += 0.10
        elif coverage >= 0.45:
            score += 0.05
        if self._is_specific_phrase(keyword):
            score += 0.03
        return min(score, 0.9)

    def _extract_core_name(self, name: str) -> str:
        """提取核心名称（去除常见后缀）"""
        suffixes = ["局", "委", "办", "中心", "站", "所", "院", "会"]
        core = name
        for suffix in suffixes:
            if core.endswith(suffix) and len(core) > len(suffix):
                core = core[:-len(suffix)]
        return core

    def _build_name_variants(self, name: str) -> List[str]:
        pending = [str(name or "").strip()]
        seen_raw: Set[str] = set()
        normalized_variants: Set[str] = set()

        removable_tokens = ("（本部）", "(本部)", "本部", "（本级）", "(本级)", "本级")
        removable_suffixes = (
            "单位",
            "部门",
            "委员会",
            "人民政府",
            "街道办事处",
            "办事处",
            "办公室",
            "执法大队",
        )

        while pending:
            candidate = pending.pop()
            if not candidate or candidate in seen_raw:
                continue
            seen_raw.add(candidate)

            normalized = self._normalize_match_text(candidate)
            if normalized and (normalized == self._normalize_match_text(name) or not self._is_generic_phrase(normalized)):
                normalized_variants.add(normalized)

            stripped_admin = self._strip_admin_prefix(candidate)
            stripped_admin_norm = self._normalize_match_text(stripped_admin)
            if stripped_admin_norm and not self._is_generic_phrase(stripped_admin_norm):
                normalized_variants.add(stripped_admin_norm)

            for token in removable_tokens:
                if token in candidate:
                    pending.append(candidate.replace(token, ""))

            for bracket_open, bracket_close in (("（", "）"), ("(", ")")):
                if bracket_open in candidate and bracket_close in candidate:
                    prefix, _, remainder = candidate.partition(bracket_open)
                    alias, _, suffix = remainder.partition(bracket_close)
                    if prefix.strip():
                        pending.append(prefix.strip() + suffix.strip())
                    if alias.strip():
                        pending.append(alias.strip())

            for suffix in removable_suffixes:
                if candidate.endswith(suffix) and len(candidate) > len(suffix):
                    pending.append(candidate[:-len(suffix)])

            if "人民政府" in candidate:
                pending.append(candidate.replace("人民政府", ""))

        return sorted(normalized_variants, key=len, reverse=True)

    def _normalize_match_text(self, text: object) -> str:
        value = str(text or "").strip()
        value = re.sub(r"20\d{2}(?:年度|年)?", "", value)
        value = re.sub(r"\d{2}(?:年度|年)", "", value)
        value = re.sub(r"(预算|决算|报告|公开|年度|pdf)", "", value, flags=re.IGNORECASE)
        value = re.sub(r"[\s（）()【】\[\]<>《》·,，、.\-_/]+", "", value)
        return value

    def _strip_admin_prefix(self, text: object) -> str:
        value = str(text or "")
        for token in _ADMIN_PREFIXES:
            value = value.replace(token, "")
        return value

    def _is_generic_phrase(self, phrase: str) -> bool:
        normalized = self._normalize_match_text(phrase)
        if not normalized:
            return True
        if normalized in _GENERIC_VARIANTS:
            return True
        stripped = self._normalize_match_text(self._strip_admin_prefix(normalized))
        return len(stripped) <= 2

    def _is_specific_phrase(self, phrase: str) -> bool:
        return not self._is_generic_phrase(phrase)

    def _contains_specific_entity_cue(self, text: str) -> bool:
        haystack = str(text or "")
        tokens = (
            "单位",
            "本级",
            "本部",
            "中心",
            "服务中心",
            "事务",
            "管理中心",
            "管理所",
            "休养所",
            "学校",
            "医院",
            "所",
            "院",
            "馆",
            "站",
            "办事处",
            "街道",
            "镇",
            "联合会",
            "委员会",
            "办公室",
            "法院",
            "检察院",
        )
        return any(token in haystack for token in tokens)
    
    def _fuzzy_match(self, name: str, text: str, min_chars: int = 3) -> bool:
        """模糊匹配：检查是否有足够长的连续子串匹配"""
        name_core = self._normalize_match_text(self._strip_admin_prefix(name))
        text_core = self._normalize_match_text(self._strip_admin_prefix(text))
        if not name_core or not text_core:
            return False

        effective_min_chars = min_chars
        if len(name_core) >= 4:
            effective_min_chars = 4
        if len(name_core) < effective_min_chars or len(text_core) < effective_min_chars:
            return False

        for i in range(len(name_core) - effective_min_chars + 1):
            substr = name_core[i:i + effective_min_chars]
            if substr in text_core:
                return True
        return False
    
    def suggest_matches(self, filename: str, first_page_text: str = "", top_n: int = 5) -> List[Tuple[Organization, float]]:
        """
        返回多个可能的匹配建议
        
        Returns:
            [(组织, 置信度), ...] 按置信度降序排列
        """
        raw_search_text = f"{filename} {first_page_text}"
        search_text = self._clean_text(raw_search_text)
        
        organizations = self.storage.get_all()
        matches = []
        
        for org in organizations:
            score = self._calculate_match_score(org, search_text, raw_search_text=raw_search_text)
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
