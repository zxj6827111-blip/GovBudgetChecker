"""
AI规则运行器
执行基于AI的规则检查
"""
import logging
import time
from typing import List, Dict, Any
from src.schemas.issues import JobContext, IssueItem

logger = logging.getLogger(__name__)


async def run_ai_rules_batch(doc: Any, 
                           config: Any) -> List[IssueItem]:
    """
    批量运行AI规则
    
    Args:
        doc: 文档对象
        config: 分析配置
        
    Returns:
        List[IssueItem]: AI检查结果
    """
    logger.info("Running AI rules for document")
    
    # 模拟AI检测结果
    results = []
    
    # 创建一个示例AI检测结果
    if hasattr(doc, 'page_texts') and doc.page_texts:
        # 检查是否有预算执行相关内容
        for page_idx, text in enumerate(doc.page_texts[:3]):  # 只检查前3页
            if text and ('预算' in text or '执行' in text or '决算' in text):
                issue = IssueItem(
                    id=f"ai_issue_{page_idx}_{int(time.time())}",
                    source="ai",
                    rule_id=f"AI-BUDGET-{page_idx:02d}",
                    severity="medium",
                    title=f"第{page_idx+1}页预算执行情况需要关注",
                    message=f"AI检测到第{page_idx+1}页存在预算执行相关内容，建议进一步核查具体数据的准确性和完整性。",
                    evidence=[{
                        "type": "text_content",
                        "page": page_idx + 1,
                        "text": text[:200] + "..." if len(text) > 200 else text
                    }],
                    location={
                        "page": page_idx + 1,
                        "section": "预算执行情况"
                    },
                    metrics={
                        "confidence": 0.75,
                        "text_length": len(text)
                    },
                    suggestion="建议核查相关数据的准确性",
                    tags=["预算执行", "AI检测", "数据核查"],
                    page_number=page_idx + 1,
                    text_snippet=text[:100] + "..." if len(text) > 100 else text
                )
                results.append(issue)
    
    logger.info(f"AI rules completed, found {len(results)} issues")
    return results