"""
AI 定位服务
当引擎侧出现 NO_ANCHOR/MULTI_ANCHOR 时，使用 AI 定位候选页码和摘录
"""
import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

from src.schemas.issues import JobContext, IssueItem
from src.services.ai_client import AIClient

logger = logging.getLogger(__name__)


@dataclass
class LocationCandidate:
    """定位候选结果"""
    page: int
    text: str
    score: float
    bbox: Optional[List[float]] = None


class AILocator:
    """AI 定位器"""
    
    def __init__(self):
        self.ai_client = AIClient()
    
    async def enhance_finding(self, 
                             job_context: JobContext,
                             finding: IssueItem) -> IssueItem:
        """
        使用 AI 增强引擎结果
        
        Args:
            job_context: 作业上下文
            finding: 需要增强的结果
            
        Returns:
            IssueItem: 增强后的结果
        """
        try:
            # 生成定位提示词
            prompt = self._generate_locator_prompt(finding, job_context)
            
            # 调用 AI
            response = await self.ai_client.chat(
                messages=[
                    {
                        "role": "system",
                        "content": self._get_locator_system_prompt()
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0.1,
                max_tokens=1000
            )
            
            # 解析候选位置
            candidates = self._parse_location_response(response.get("content", ""))
            
            if candidates:
                # 使用最佳候选位置更新结果
                best_candidate = candidates[0]
                
                enhanced_finding = finding.copy()
                enhanced_finding.page_number = best_candidate.page
                enhanced_finding.evidence = {
                    "text_snippet": best_candidate.text,
                    "bbox": best_candidate.bbox
                }
                
                # 更新 why_not 说明已通过 AI 定位
                enhanced_finding.why_not = f"AI_LOCATED: score={best_candidate.score:.2f}"
                
                logger.info(f"AI locator enhanced finding {finding.rule_id} with score {best_candidate.score}")
                return enhanced_finding
            else:
                logger.warning(f"AI locator found no candidates for finding {finding.rule_id}")
                return finding
                
        except Exception as e:
            logger.error(f"AI locator enhancement failed: {e}")
            return finding
    
    def _generate_locator_prompt(self, finding: IssueItem, job_context: JobContext) -> str:
        """生成定位提示词"""
        
        # 截取文档内容避免过长
        doc_content = job_context.ocr_text[:3000] if job_context.ocr_text else "无OCR文本"
        
        prompt = f"""
请在以下文档中定位与问题相关的文本位置。

## 问题信息
- 规则ID: {finding.rule_id}
- 问题标题: {finding.title}
- 问题描述: {finding.description}
- 失败原因: {finding.why_not}

## 文档内容
{doc_content}

## 任务要求
请找出文档中与该问题最相关的文本片段，按相关性排序返回前3个候选位置。

输出格式（JSON数组）:
[
  {{
    "page": 1,
    "text": "相关文本摘录",
    "score": 0.95
  }},
  {{
    "page": 2, 
    "text": "另一个相关文本",
    "score": 0.80
  }}
]

如果找不到相关内容，返回空数组 []
"""
        return prompt
    
    def _get_locator_system_prompt(self) -> str:
        """获取定位系统提示词"""
        return """你是一个专业的文档定位助手。你的任务是在文档中找到与给定问题最相关的文本位置。

要求：
1. 仔细分析问题描述和失败原因
2. 在文档中寻找相关的关键词、数字、表格标题等
3. 按相关性评分（0-1之间）
4. 只返回有效的JSON格式
5. text字段必须是文档中的原文摘录"""
    
    def _parse_location_response(self, content: str) -> List[LocationCandidate]:
        """解析定位响应"""
        import json
        import re
        
        candidates = []
        
        try:
            # 尝试直接解析 JSON
            data = json.loads(content)
            
            for item in data:
                if isinstance(item, dict) and all(k in item for k in ['page', 'text', 'score']):
                    candidate = LocationCandidate(
                        page=int(item['page']),
                        text=str(item['text']),
                        score=float(item['score'])
                    )
                    candidates.append(candidate)
            
        except json.JSONDecodeError:
            # 尝试提取 JSON 代码块
            json_match = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', content, re.DOTALL)
            if json_match:
                try:
                    data = json.loads(json_match.group(1))
                    for item in data:
                        if isinstance(item, dict) and all(k in item for k in ['page', 'text', 'score']):
                            candidate = LocationCandidate(
                                page=int(item['page']),
                                text=str(item['text']),
                                score=float(item['score'])
                            )
                            candidates.append(candidate)
                except json.JSONDecodeError:
                    pass
        
        # 按分数排序
        candidates.sort(key=lambda x: x.score, reverse=True)
        
        return candidates


# 便捷函数
async def locate_with_ai(job_context: JobContext, 
                        finding: IssueItem) -> IssueItem:
    """便捷的 AI 定位函数"""
    locator = AILocator()
    return await locator.enhance_finding(job_context, finding)