"""
智谱 AI 提供商实现
支持 GLM-4 系列模型
"""
import asyncio
import json
import time
from typing import Dict, List, Any, Optional, AsyncIterator

import aiohttp

from .base import BaseLLMProvider, LLMResponse, LLMError, LLMErrorType


class ZhipuProvider(BaseLLMProvider):
    """智谱 AI 提供商"""
    
    def __init__(self, 
                 api_key: str,
                 base_url: str = "https://open.bigmodel.cn/api/paas/v4",
                 default_model: str = "glm-4.5-flash",
                 timeout: int = 60,
                 max_retries: int = 1):
        
        # 模型别名映射
        model_aliases = {
            "glm-4.5-flash": "glm-4.5-flash",
            "glm-4-flash": "glm-4-flash", 
            "glm-4.5": "glm-4.5",
            "glm-4": "glm-4",
            # 兼容性别名
            "zhipu_flash": "glm-4.5-flash",
            "zhipu_reasoner": "glm-4.5"
        }
        
        super().__init__(
            base_url=base_url,
            api_key=api_key,
            default_model=default_model,
            timeout=timeout,
            max_retries=max_retries,
            model_aliases=model_aliases
        )
    
    @property
    def name(self) -> str:
        return "zhipu"
    
    @property
    def models(self) -> List[str]:
        return [
            "glm-4.5-flash",
            "glm-4-flash",
            "glm-4.5",
            "glm-4"
        ]
    
    def format_messages(self, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """格式化消息，智谱格式"""
        formatted = []
        for msg in messages:
            formatted.append({
                "role": msg["role"],
                "content": msg["content"]
            })
        return formatted
    
    async def chat(self, 
                   messages: List[Dict[str, str]], 
                   model: Optional[str] = None,
                   temperature: float = 0.2,
                   max_tokens: Optional[int] = None,
                   timeout: int = 60,
                   **kwargs) -> LLMResponse:
        """聊天接口"""
        start_time = time.time()
        
        try:
            model = self.resolve_model(model)
            formatted_messages = self.format_messages(messages)
            
            payload = {
                "model": model,
                "messages": formatted_messages,
                "temperature": min(max(temperature, 0.0), 1.0),
                "stream": False
            }
            
            if max_tokens:
                payload["max_tokens"] = max_tokens
            
            # 添加其他参数
            if "top_p" in kwargs:
                payload["top_p"] = kwargs["top_p"]
            if "presence_penalty" in kwargs:
                payload["presence_penalty"] = kwargs["presence_penalty"]
            
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
                async with session.post(
                    f"{self.base_url}/chat/completions",
                    headers=self.get_headers(),
                    json=payload
                ) as response:
                    
                    response_data = await response.json()
                    latency_ms = int((time.time() - start_time) * 1000)
                    
                    if response.status != 200:
                        error = self.parse_error(response.status, response_data)
                        error.model = model
                        self.update_stats(latency_ms, success=False)
                        raise error
                    
                    # 解析响应
                    choice = response_data["choices"][0]
                    content = choice["message"]["content"]
                    finish_reason = choice.get("finish_reason", "stop")
                    usage = response_data.get("usage", {})
                    
                    self.update_stats(latency_ms, success=True)
                    
                    return LLMResponse(
                        content=content,
                        model=model,
                        provider=self.name,
                        usage=usage,
                        latency_ms=latency_ms,
                        finish_reason=finish_reason,
                        request_id=response_data.get("id")
                    )
                    
        except aiohttp.ClientTimeout:
            latency_ms = int((time.time() - start_time) * 1000)
            self.update_stats(latency_ms, success=False)
            raise LLMError(
                error_type=LLMErrorType.TIMEOUT,
                message=f"Request timeout after {timeout}s",
                provider=self.name,
                model=model
            )
        except aiohttp.ClientError as e:
            latency_ms = int((time.time() - start_time) * 1000)
            self.update_stats(latency_ms, success=False)
            raise LLMError(
                error_type=LLMErrorType.NETWORK_ERROR,
                message=f"Network error: {str(e)}",
                provider=self.name,
                model=model
            )
        except LLMError:
            raise
        except Exception as e:
            latency_ms = int((time.time() - start_time) * 1000)
            self.update_stats(latency_ms, success=False)
            raise LLMError(
                error_type=LLMErrorType.UNKNOWN,
                message=f"Unexpected error: {str(e)}",
                provider=self.name,
                model=model
            )
    
    async def chat_stream(self, 
                         messages: List[Dict[str, str]], 
                         model: Optional[str] = None,
                         temperature: float = 0.2,
                         max_tokens: Optional[int] = None,
                         timeout: int = 60,
                         **kwargs) -> AsyncIterator[Dict[str, Any]]:
        """流式聊天接口"""
        model = self.resolve_model(model)
        formatted_messages = self.format_messages(messages)
        
        payload = {
            "model": model,
            "messages": formatted_messages,
            "temperature": min(max(temperature, 0.0), 1.0),
            "stream": True
        }
        
        if max_tokens:
            payload["max_tokens"] = max_tokens
        
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
                async with session.post(
                    f"{self.base_url}/chat/completions",
                    headers=self.get_headers(),
                    json=payload
                ) as response:
                    
                    if response.status != 200:
                        response_data = await response.json()
                        error = self.parse_error(response.status, response_data)
                        error.model = model
                        raise error
                    
                    async for line in response.content:
                        line = line.decode('utf-8').strip()
                        if not line or not line.startswith('data: '):
                            continue
                        
                        data_str = line[6:]  # 移除 'data: ' 前缀
                        if data_str == '[DONE]':
                            break
                        
                        try:
                            data = json.loads(data_str)
                            yield {
                                "provider": self.name,
                                "model": model,
                                "data": data
                            }
                        except json.JSONDecodeError:
                            continue
                            
        except aiohttp.ClientTimeout:
            raise LLMError(
                error_type=LLMErrorType.TIMEOUT,
                message=f"Stream timeout after {timeout}s",
                provider=self.name,
                model=model
            )
        except aiohttp.ClientError as e:
            raise LLMError(
                error_type=LLMErrorType.NETWORK_ERROR,
                message=f"Network error: {str(e)}",
                provider=self.name,
                model=model
            )
    
    def parse_error(self, status_code: int, response_data: Dict[str, Any]) -> LLMError:
        """解析智谱特定的错误响应"""
        error_info = response_data.get('error', {})
        error_code = error_info.get('code', '')
        error_msg = error_info.get('message', 'Unknown error')
        
        # 智谱特定错误码映射
        if error_code == 'invalid_api_key' or status_code in [401, 403]:
            error_type = LLMErrorType.AUTHENTICATION
        elif error_code == 'model_not_found' or status_code == 404:
            error_type = LLMErrorType.MODEL_NOT_FOUND
        elif error_code == 'rate_limit_exceeded' or status_code == 429:
            error_type = LLMErrorType.RATE_LIMIT
        elif error_code == 'quota_exceeded':
            error_type = LLMErrorType.QUOTA_EXCEEDED
        elif status_code == 408:
            error_type = LLMErrorType.TIMEOUT
        elif 500 <= status_code < 600:
            error_type = LLMErrorType.SERVER_ERROR
        elif status_code == 400:
            error_type = LLMErrorType.INVALID_REQUEST
        else:
            error_type = LLMErrorType.UNKNOWN
        
        return LLMError(
            error_type=error_type,
            message=f"Zhipu API Error: {error_msg}",
            status_code=status_code,
            provider=self.name
        )