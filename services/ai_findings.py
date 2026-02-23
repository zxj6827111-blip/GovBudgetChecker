"""
AI检查服务
直接调用 LLM 进行合规检查，生成统一的 IssueItem 格式结果
"""
import logging
import json
import time
import asyncio
from typing import List, Dict, Any, Optional
import traceback
import re

from schemas.issues import IssueItem, JobContext, AnalysisConfig
from engine.ai.extractor_client import ExtractorClient  # 复用现有AI客户端

logger = logging.getLogger(__name__)


class AIFindingsService:
    """AI检查服务"""
    
    def __init__(self, config: AnalysisConfig):
        self.config = config
        self.ai_client = ExtractorClient()  # 复用现有AI客户端
        self.ai_errors = []  # 聚合AI错误信息
    
    async def analyze(self, context: JobContext) -> List[IssueItem]:
        """执行AI分析"""
        # 确保配置项有合理的默认值
        ai_enabled = getattr(self.config, 'ai_enabled', True)
        if not ai_enabled:
            logger.info("AI分析已禁用")
            return []
        
        start_time = time.time()
        logger.info(f"开始AI分析: job_id={context.job_id}")
        
        # 重置错误计数
        self.ai_errors = []
        
        try:
            # 合并所有页面文本用于语义审计，同时计算页码偏移量
            page_texts = context.page_texts if context.page_texts else []
            if not page_texts:
                logger.warning("文档文本为空，跳过AI语义审计")
                return []
            
            # 计算每页的起始偏移量（用于后续页码映射）
            page_offsets = []  # [(start_offset, end_offset, page_number), ...]
            current_offset = 0
            for i, text in enumerate(page_texts):
                page_start = current_offset
                page_end = current_offset + len(text)
                page_offsets.append((page_start, page_end, i + 1))  # 页码从1开始
                current_offset = page_end + 1  # +1 for \n separator
            
            all_text = "\n".join(page_texts)
            if not all_text.strip():
                logger.warning("文档文本为空，跳过AI语义审计")
                return []
            
            # 生成文档哈希
            import hashlib
            doc_hash = hashlib.sha1(all_text[:5000].encode('utf-8')).hexdigest()[:12]
            
            # 调用真实的AI语义审计接口
            semantic_issues = await self.ai_client.ai_semantic_audit(all_text[:15000], doc_hash)
            
            # 转换为IssueItem格式，传入页码偏移量
            issues = self._convert_semantic_issues_to_items(semantic_issues, context, page_offsets)
            
            elapsed = time.time() - start_time
            logger.info(f"AI分析完成: job_id={context.job_id}, issues={len(issues)}, elapsed={elapsed:.2f}s")
            
            return issues
            
        except Exception as e:
            logger.error(f"AI分析失败: job_id={context.job_id}, error={e}")
            logger.error(traceback.format_exc())
            
            # 记录分析失败错误
            self.ai_errors.append({
                "type": "analysis_failure",
                "message": str(e),
                "timestamp": time.time()
            })
            
            return []
    
    def _convert_semantic_issues_to_items(self, semantic_issues: List[Dict[str, Any]], context: JobContext, page_offsets: List[tuple] = None) -> List[IssueItem]:
        """将语义问题转换为IssueItem格式"""
        issues = []
        
        # 如果没有页码偏移量，使用默认值
        if not page_offsets:
            page_offsets = [(0, 999999, 1)]
        
        for idx, issue in enumerate(semantic_issues):
            try:
                # 映射错误类型到严重程度
                severity_map = {
                    "错别字": "high",
                    "重复": "medium",
                    "表达不当": "medium",
                    "规范性": "low"
                }
                
                issue_type = issue.get("type", "其他")
                severity = severity_map.get(issue_type, "medium")
                
                # 根据 span 计算所属页码
                span = issue.get("span", [0, 0])
                span_start = span[0] if len(span) > 0 else 0
                span_end = span[1] if len(span) > 1 else span_start
                
                # 遍历页码偏移量，找到 span 所属的页码
                page_number = 1
                for page_start, page_end, page_num in page_offsets:
                    if span_start >= page_start and span_start < page_end:
                        page_number = page_num
                        break
                
                # 构建位置信息
                location = {
                    "page": page_number,
                    "span_start": span_start,
                    "span_end": span_end
                }
                
                # 构建证据
                evidence = [{
                    "type": "text_content",
                    "text": issue.get("context", ""),
                    "original": issue.get("original", "")
                }]
                
                # 生成唯一ID
                issue_id = f"ai_semantic_{context.job_id}_{idx}_{int(time.time())}"
                
                item = IssueItem(
                    id=issue_id,
                    source="ai",
                    rule_id=f"AI-SEM-{issue_type[:2].upper()}-{idx:03d}",
                    severity=severity,
                    title=f"{issue_type}：{issue.get('original', '')[:20]}",
                    message=f"发现{issue_type}问题：「{issue.get('original', '')}」，建议修改为「{issue.get('suggestion', '')}」",
                    evidence=evidence,
                    location=location,
                    page_number=page_number,  # 使用计算出的准确页码
                    suggestion=issue.get("suggestion", ""),
                    tags=[issue_type, "AI检测", "语义审计"],
                    created_at=time.time()
                )
                issues.append(item)
                
            except Exception as e:
                logger.warning(f"转换语义问题失败: {e}, issue={issue}")
                continue
        
        return issues
    
    def _build_prompt(self, context: JobContext) -> str:
        """构建AI提示词"""
        # 确保所有属性都有合理的默认值
        pages = getattr(context, 'pages', 0)
        ocr_text = getattr(context, 'ocr_text', '')
        tables = getattr(context, 'tables', [])
        
        prompt = f"""
你是一个专业的政府预决算合规检查专家。请对以下预决算文档进行全面的合规性和一致性检查。

## 文档信息
- 文件路径: {context.pdf_path}
- 页数: {pages}
- OCR文本长度: {len(ocr_text or '') // 1000}K字符

## 检查要求
请重点检查以下方面，发现问题时必须提供具体的页码和文本证据：

1. **九张表齐全性**: 检查是否包含完整的预决算表格
2. **目录-正文一致性**: 检查目录与正文内容是否对应
3. **表内勾稽关系**: 检查总表恒等式和明细汇总
4. **预算vs决算差异**: 检查预算执行情况的合理性
5. **比例和百分比**: 检查各项比例计算是否正确
6. **年份和页码**: 检查年份标注和页码连续性
7. **三公经费口径**: 检查三公经费统计口径一致性
8. **政府采购口径**: 检查政府采购统计规范性
9. **类款项编码**: 检查预算科目编码规范性

## OCR文本内容
{ocr_text[:10000] if ocr_text else "无OCR文本"}

## 表格数据
{json.dumps(tables[:5], ensure_ascii=False, indent=2) if tables else "无表格数据"}

## 输出格式要求
请严格按照以下JSON格式输出，每个问题必须包含所有必需字段：

```json
[
  {{"rule_id": "AI-XXX-001",
    "title": "问题标题",
    "message": "详细问题描述",
    "severity": "critical|high|medium|low|info",
    "page": 页码数字,
    "section": "所在章节",
    "table": "所在表格名称",
    "evidence": "具体的文本证据摘录",
    "metrics": {{
      "expected": 期望值,
      "actual": 实际值,
      "diff": 差异值,
      "pct": 百分比
    }},
    "suggestion": "改进建议",
    "tags": ["标签1", "标签2"],
    "category": "问题分类"
  }}
]
```

## 重要约束
1. 必须基于实际文本内容，不得编造问题
2. 每个问题必须提供具体的页码和文本证据
3. 金额数据必须准确，不得估算
4. 严重程度要合理评估
5. 建议要具体可操作
6. 如果没有发现问题，返回空数组 []

请开始检查：
"""
        return prompt
    
    async def _call_ai_with_retry(self, prompt: str, context: JobContext) -> str:
        """带重试的AI调用"""
        last_error = None
        
        for attempt in range(self.config.ai_retry + 1):
            try:
                logger.info(f"AI调用尝试 {attempt + 1}/{self.config.ai_retry + 1}")
                
                # 使用现有的AI客户端
                response = await asyncio.wait_for(
                    self._call_ai_client(prompt),
                    timeout=self.config.ai_timeout
                )
                
                if response and response.strip():
                    return response
                else:
                    raise ValueError("AI返回空响应")
                    
            except asyncio.TimeoutError:
                last_error = f"AI调用超时 ({self.config.ai_timeout}s)"
                logger.warning(f"{last_error}, 尝试 {attempt + 1}")
            except Exception as e:
                last_error = f"AI调用失败: {e}"
                logger.warning(f"{last_error}, 尝试 {attempt + 1}")
            
            if attempt < self.config.ai_retry:
                await asyncio.sleep(2 ** attempt)  # 指数退避
        
        raise Exception(f"AI调用最终失败: {last_error}")
    
    async def _call_ai_client(self, prompt: str) -> str:
        """调用AI客户端"""
        try:
            # 注意：现有的ExtractorClient不支持chat_completion方法
            # 这里提供一个默认实现，返回模拟结果
            logger.warning("使用模拟AI响应，因为ExtractorClient不支持chat_completion方法")
            return self._get_mock_ai_response()
        except Exception as e:
            logger.error(f"AI客户端调用失败: {e}")
            raise
    
    # 移除同步AI调用方法，所有AI调用都应该使用异步方式
    # async def _sync_call_ai(self, prompt: str) -> str:
    #     """同步AI调用（兼容现有代码）"""
    #     try:
    #         # 这里需要根据实际的AI客户端接口调整
    #         # 返回模拟结果用于测试
    #         return self._get_mock_ai_response()
    #     except Exception as e:
    #         logger.error(f"同步AI调用失败: {e}")
    #         raise
    
    def _get_mock_ai_response(self) -> str:
        """获取模拟AI响应（用于测试）"""
        mock_response = [
            {
                "rule_id": "AI-COMP-001",
                "title": "预算收支总表缺失",
                "message": "未在文档中发现完整的预算收支总表，可能影响预算执行情况的全面了解",
                "severity": "high",
                "page": 1,
                "section": "预算表",
                "table": "预算收支总表",
                "evidence": "第1页目录显示应有预算收支总表，但正文中未找到对应表格",
                "metrics": {
                    "expected": 1,
                    "actual": 0,
                    "diff": 1
                },
                "suggestion": "请补充完整的预算收支总表，包含收入和支出的详细分类",
                "tags": ["表格完整性", "预算表"],
                "category": "表格缺失"
            },
            {
                "rule_id": "AI-CALC-001", 
                "title": "收入明细汇总不符",
                "message": "各项收入明细汇总与总收入金额不一致，存在计算错误",
                "severity": "medium",
                "page": 3,
                "section": "收入明细",
                "table": "收入明细表",
                "evidence": "第3页收入明细表显示：税收收入800万+非税收入150万=950万，但总收入显示1000万",
                "metrics": {
                    "expected": 10000000,
                    "actual": 9500000,
                    "diff": 500000,
                    "pct": 5.0
                },
                "suggestion": "请核对收入明细计算，确保各项明细汇总与总额一致",
                "tags": ["金额一致性", "收入"],
                "category": "计算错误"
            }
        ]
        return json.dumps(mock_response, ensure_ascii=False, indent=2)
    
    def _parse_ai_response(self, response: str, context: JobContext) -> List[IssueItem]:
        """解析AI响应"""
        discarded_count = 0
        discarded_examples = []
        
        try:
            # 提取JSON内容
            json_content = self._extract_json_from_response(response)
            if not json_content:
                logger.warning("AI响应中未找到有效JSON")
                self.ai_errors.append({
                    "type": "json_extraction_failed",
                    "message": "AI响应中未找到有效JSON",
                    "response_preview": response[:200] + "..." if len(response) > 200 else response
                })
                return []
            
            # 解析JSON
            raw_issues = json.loads(json_content)
            if not isinstance(raw_issues, list):
                logger.warning("AI响应JSON格式错误，应为数组")
                self.ai_errors.append({
                    "type": "json_format_error",
                    "message": "AI响应JSON格式错误，应为数组",
                    "actual_type": type(raw_issues).__name__
                })
                return []
            
            # 转换为IssueItem
            issues = []
            for idx, raw_issue in enumerate(raw_issues):
                try:
                    issue = self._convert_ai_issue(raw_issue, context, idx)
                    if issue:
                        issues.append(issue)
                    else:
                        discarded_count += 1
                        if len(discarded_examples) < 3:  # 只保存前3个示例
                            discarded_examples.append({
                                "index": idx,
                                "issue": raw_issue,
                                "reason": "conversion_failed"
                            })
                except Exception as e:
                    logger.error(f"转换AI问题失败: {e}, issue={raw_issue}")
                    discarded_count += 1
                    if len(discarded_examples) < 3:
                        discarded_examples.append({
                            "index": idx,
                            "issue": raw_issue,
                            "reason": f"exception: {str(e)}"
                        })
            
            # 记录丢弃统计
            if discarded_count > 0:
                self.ai_errors.append({
                    "type": "items_discarded",
                    "count": discarded_count,
                    "total_items": len(raw_issues),
                    "examples": discarded_examples,
                    "message": f"因格式问题丢弃了 {discarded_count}/{len(raw_issues)} 个AI检测结果"
                })
                logger.warning(f"因格式问题丢弃了 {discarded_count}/{len(raw_issues)} 个AI检测结果")
            
            return issues
            
        except json.JSONDecodeError as e:
            logger.error(f"AI响应JSON解析失败: {e}")
            self.ai_errors.append({
                "type": "json_decode_error",
                "message": f"JSON解析失败: {str(e)}",
                "response_preview": response[:200] + "..." if len(response) > 200 else response
            })
            return []
        except Exception as e:
            logger.error(f"解析AI响应失败: {e}")
            self.ai_errors.append({
                "type": "parse_error",
                "message": f"解析失败: {str(e)}",
                "response_preview": response[:200] + "..." if len(response) > 200 else response
            })
            return []
    
    def _extract_json_from_response(self, response: str) -> Optional[str]:
        """从响应中提取JSON内容"""
        # 尝试直接解析
        response = response.strip()
        if response.startswith('[') and response.endswith(']'):
            return response
        
        # 尝试从代码块中提取
        json_pattern = r'```(?:json)?\s*(\[.*?\])\s*```'
        match = re.search(json_pattern, response, re.DOTALL)
        if match:
            return match.group(1)
        
        # 尝试查找数组结构
        array_pattern = r'(\[.*?\])'
        match = re.search(array_pattern, response, re.DOTALL)
        if match:
            return match.group(1)
        
        return None
    
    def _convert_ai_issue(self, raw_issue: Dict[str, Any], context: JobContext, idx: int) -> Optional[IssueItem]:
        """转换AI问题为IssueItem"""
        try:
            # 验证必需字段
            required_fields = ["title", "message", "severity"]
            missing_fields = []
            for field in required_fields:
                if field not in raw_issue:
                    missing_fields.append(field)
            
            if missing_fields:
                logger.warning(f"AI问题缺少必需字段: {missing_fields}")
                self.ai_errors.append({
                    "type": "missing_required_fields",
                    "missing_fields": missing_fields,
                    "issue_index": idx,
                    "issue_preview": str(raw_issue)[:100] + "..." if len(str(raw_issue)) > 100 else str(raw_issue)
                })
                return None
            
            # 提取基本信息
            rule_id = raw_issue.get("rule_id", f"AI-GEN-{idx:03d}")
            title = raw_issue["title"]
            message = raw_issue["message"]
            severity = self._normalize_severity(raw_issue["severity"])
            
            # 构建位置信息
            location = {
                "page": raw_issue.get("page", 0),
                "section": raw_issue.get("section", ""),
                "table": raw_issue.get("table", ""),
                "row": raw_issue.get("row", ""),
                "col": raw_issue.get("col", "")
            }
            
            # 构建证据
            evidence = []
            if "evidence" in raw_issue:
                evidence.append({
                    "page": location["page"],
                    "text": raw_issue["evidence"],
                    "bbox": raw_issue.get("bbox")
                })
            
            # 构建指标
            metrics = raw_issue.get("metrics", {})
            
            # 构建标签
            tags = raw_issue.get("tags", [])
            if "category" in raw_issue:
                tags.append(raw_issue["category"])
            
            # 生成唯一ID
            issue_id = IssueItem.create_id("ai", rule_id, location)
            
            return IssueItem(
                id=issue_id,
                source="ai",
                rule_id=rule_id,
                severity=severity,
                title=title,
                message=message,
                evidence=evidence,
                location=location,
                metrics=metrics,
                suggestion=raw_issue.get("suggestion"),
                tags=tags,
                created_at=time.time()
            )
            
        except Exception as e:
            logger.error(f"转换AI问题失败: {e}")
            self.ai_errors.append({
                "type": "conversion_error",
                "error": str(e),
                "issue_index": idx,
                "issue_preview": str(raw_issue)[:100] + "..." if len(str(raw_issue)) > 100 else str(raw_issue)
            })
            return None
    
    def _normalize_severity(self, severity: str) -> str:
        """标准化严重程度"""
        severity_map = {
            "critical": "critical",
            "high": "high",
            "medium": "medium", 
            "low": "low",
            "info": "info"
        }
        
        return severity_map.get(severity.lower(), "medium")


async def analyze_with_ai(context: JobContext, config: AnalysisConfig) -> List[IssueItem]:
    """使用AI分析的便捷函数"""
    service = AIFindingsService(config)
    return await service.analyze(context)