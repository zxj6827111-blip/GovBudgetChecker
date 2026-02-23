#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI抽取器客户端 - 后端调用AI抽取器微服务的客户端
"""

import os
import asyncio
import logging
from typing import List, Dict, Any, Optional
import hashlib
import httpx
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ==================== 配置 ====================
AI_EXTRACTOR_URL = os.getenv("AI_EXTRACTOR_URL", "http://127.0.0.1:9009/ai/extract/v1")
AI_ASSIST_ENABLED = os.getenv("AI_ASSIST_ENABLED", "true").lower() == "true"

# 超时和重试配置
REQUEST_TIMEOUT = 120.0  # 增加到120秒，适应复杂文档处理
MAX_RETRIES = 2
RETRY_DELAY = 1.0

@dataclass
class ExtractorConfig:
    """抽取器配置"""
    url: str = AI_EXTRACTOR_URL
    enabled: bool = AI_ASSIST_ENABLED
    timeout: float = REQUEST_TIMEOUT
    max_retries: int = MAX_RETRIES
    retry_delay: float = RETRY_DELAY

class ExtractorClient:
    """AI抽取器客户端"""
    
    def __init__(self, config: Optional[ExtractorConfig] = None):
        self.config = config or ExtractorConfig()
        
    async def ai_extract_pairs(self, section_text: str, doc_hash: str) -> List[Dict[str, Any]]:
        """
        调用AI抽取器进行信息抽取
        
        Args:
            section_text: （三）小节全文
            doc_hash: 文档哈希
            
        Returns:
            抽取结果列表，每个元素包含：
            - budget_text: 预算数字原文
            - budget_span: 预算数字span [start, end)
            - final_text: 决算数字原文  
            - final_span: 决算数字span [start, end)
            - stmt_text: 比较语句原文
            - stmt_span: 比较语句span [start, end)
            - reason_text: 原因说明原文（可选）
            - reason_span: 原因说明span（可选）
            - item_title: 项目标题（可选）
            - clip: 原文截取片段
        """
        if not self.config.enabled:
            logger.debug("AI辅助未启用，返回空列表")
            return []
            
        if not section_text.strip():
            logger.debug("输入文本为空，返回空列表")
            return []
            
        try:
            result = await self._call_with_retry(section_text, doc_hash)
            return result
            
        except Exception as e:
            logger.error(f"AI抽取失败: {e}")
            # 网络失败时不影响主流程，退回纯规则模式
            return []
    
    async def _call_with_retry(self, section_text: str, doc_hash: str) -> List[Dict[str, Any]]:
        """带重试的调用"""
        last_exception = None
        
        for attempt in range(self.config.max_retries + 1):
            try:
                if attempt > 0:
                    logger.info(f"AI抽取重试第{attempt}次")
                    await asyncio.sleep(self.config.retry_delay * attempt)
                    
                return await self._single_call(section_text, doc_hash)
                
            except httpx.TimeoutException as e:
                last_exception = e
                logger.warning(f"AI抽取超时 (尝试 {attempt + 1}/{self.config.max_retries + 1}): {e}")
                
            except httpx.ConnectError as e:
                last_exception = e
                logger.warning(f"AI抽取连接失败 (尝试 {attempt + 1}/{self.config.max_retries + 1}): {e}")
                
            except Exception as e:
                last_exception = e
                logger.warning(f"AI抽取异常 (尝试 {attempt + 1}/{self.config.max_retries + 1}): {e}")
                
        # 所有重试都失败
        logger.error(f"AI抽取最终失败: {last_exception}")
        raise last_exception
    
    async def _single_call(self, section_text: str, doc_hash: str) -> List[Dict[str, Any]]:
        """单次调用"""
        request_data = {
            "task": "R33110_pairs_v1",
            "section_text": section_text,
            "language": "zh", 
            "doc_hash": doc_hash,
            "max_windows": 3
        }
        
        async with httpx.AsyncClient(timeout=self.config.timeout) as client:
            response = await client.post(
                self.config.url,
                json=request_data,
                headers={"Content-Type": "application/json"}
            )
            
            if response.status_code != 200:
                raise Exception(f"AI抽取器返回错误状态码: {response.status_code}, 响应: {response.text}")
                
            result = response.json()
            
            if "hits" not in result:
                raise Exception(f"AI抽取器返回格式错误: {result}")
                
            hits = result["hits"]
            logger.info(f"AI抽取成功，获得{len(hits)}个结果")
            
            # 转换为内部格式
            return self._convert_hits_to_internal_format(hits)
    
    def _convert_hits_to_internal_format(self, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """将API返回的hits转换为内部格式"""
        converted = []
        
        for hit in hits:
            try:
                # 验证必需字段
                required_fields = ["budget_text", "budget_span", "final_text", "final_span", "stmt_text", "stmt_span", "clip"]
                if not all(field in hit for field in required_fields):
                    logger.warning(f"跳过缺少必需字段的hit: {hit}")
                    continue
                    
                # 验证span格式
                for span_field in ["budget_span", "final_span", "stmt_span"]:
                    span = hit[span_field]
                    if not isinstance(span, list) or len(span) != 2:
                        logger.warning(f"跳过span格式错误的hit: {hit}")
                        continue
                        
                # 处理可选的reason_span
                reason_span = hit.get("reason_span")
                if reason_span and (not isinstance(reason_span, list) or len(reason_span) != 2):
                    logger.warning(f"reason_span格式错误，设为None: {reason_span}")
                    hit["reason_span"] = None
                    hit["reason_text"] = None
                
                converted.append(hit)
                
            except Exception as e:
                logger.warning(f"转换hit失败: {e}, hit: {hit}")
                continue
                
        return converted
    
    async def ai_semantic_audit(self, section_text: str, doc_hash: str) -> List[Dict[str, Any]]:
        """
        调用AI抽取器进行语义审计（错别字、重复、表达不当）
        
        Args:
            section_text: 待检查的文本内容
            doc_hash: 文档哈希
            
        Returns:
            语义问题列表，每个元素包含：
            - type: 错误类型（错别字/重复/表达不当/规范性）
            - original: 原文错误内容
            - suggestion: 修改建议
            - span: [start, end) 位置
            - context: 上下文片段
        """
        if not self.config.enabled:
            logger.debug("AI辅助未启用，返回空列表")
            return []
            
        if not section_text.strip():
            logger.debug("输入文本为空，返回空列表")
            return []
            
        try:
            result = await self._call_semantic_audit(section_text, doc_hash)
            return result
            
        except Exception as e:
            logger.error(f"AI语义审计失败: {e}")
            return []
    
    async def _call_semantic_audit(self, section_text: str, doc_hash: str) -> List[Dict[str, Any]]:
        """语义审计的单次调用"""
        request_data = {
            "task": "semantic_audit_v1",
            "section_text": section_text,
            "language": "zh", 
            "doc_hash": doc_hash,
            "max_windows": 3
        }
        
        async with httpx.AsyncClient(timeout=self.config.timeout) as client:
            response = await client.post(
                self.config.url,
                json=request_data,
                headers={"Content-Type": "application/json"}
            )
            
            if response.status_code != 200:
                raise Exception(f"AI语义审计返回错误状态码: {response.status_code}, 响应: {response.text}")
                
            result = response.json()
            
            if "hits" not in result:
                raise Exception(f"AI语义审计返回格式错误: {result}")
                
            hits = result["hits"]
            logger.info(f"AI语义审计成功，获得{len(hits)}个结果")
            
            # 提取语义问题
            semantic_issues = []
            for hit in hits:
                if "semantic_issues" in hit and hit["semantic_issues"]:
                    semantic_issues.extend(hit["semantic_issues"])
            
            return semantic_issues
    
    async def health_check(self) -> bool:
        """健康检查"""
        if not self.config.enabled:
            return False
            
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                health_url = self.config.url.replace("/ai/extract/v1", "/health")
                response = await client.get(health_url)
                return response.status_code == 200
                
        except Exception as e:
            logger.warning(f"AI抽取器健康检查失败: {e}")
            return False

# ==================== 全局实例 ====================
_default_client = None

def get_extractor_client() -> ExtractorClient:
    """获取默认的抽取器客户端实例"""
    global _default_client
    if _default_client is None:
        _default_client = ExtractorClient()
    return _default_client

async def ai_extract_pairs(section_text: str, doc_hash: str) -> List[Dict[str, Any]]:
    """
    便捷函数：调用AI抽取器进行信息抽取
    
    Args:
        section_text: （三）小节全文
        doc_hash: 文档哈希
        
    Returns:
        抽取结果列表
    """
    client = get_extractor_client()
    return await client.ai_extract_pairs(section_text, doc_hash)

def generate_doc_hash(section_text: str) -> str:
    """生成文档哈希"""
    return hashlib.sha1(section_text.encode('utf-8')).hexdigest()

# ==================== 配置更新 ====================
def update_config(
    url: Optional[str] = None,
    enabled: Optional[bool] = None,
    timeout: Optional[float] = None,
    max_retries: Optional[int] = None,
    retry_delay: Optional[float] = None
):
    """更新全局配置"""
    global _default_client
    
    if _default_client is None:
        _default_client = ExtractorClient()
        
    config = _default_client.config
    
    if url is not None:
        config.url = url
    if enabled is not None:
        config.enabled = enabled
    if timeout is not None:
        config.timeout = timeout
    if max_retries is not None:
        config.max_retries = max_retries
    if retry_delay is not None:
        config.retry_delay = retry_delay
        
    logger.info(f"AI抽取器配置已更新: enabled={config.enabled}, url={config.url}")