"""
OpenAI 兼容提供商实现
支持 OpenAI API 以及其他兼容接口
"""
import asyncio
import json
import time
from typing import Dict, List, Any, Optional, AsyncIterator

import aiohttp

from .base import BaseLLMProvider, LLMResponse, LLMError, LLMErrorType


class OpenAICompatProvider(BaseLLMProvider):
    """OpenAI 兼容提供商"""
    
    def __init__(self, 
                 api_key: str,
                 base_url: str = "https://api.openai.com/v1",
                 default_model: str = "gpt-4o-mini",
                 timeout: int = 60,
                 max_retries: int = 1,
                 organization: Optional[str] = None):
        
        # 模型别名映射
        model_aliases = {
            "gpt-4o-mini": "gpt-4o-mini",
            "gpt-4o": "gpt-4o",
            "gpt-4-turbo": "gpt-4-turbo",
            "gpt-4": "gpt-4",
            "gpt-3.5-turbo": "gpt-3.5-turbo",
            # 兼容性别名
            "openai_fallback": "gpt-4o-mini",
            "openai_mini": "gpt-4o-mini",
            "openai_4o": "gpt-4o"
        }
        
        super().__init__(
            base_url=base_url,
            api_key=api_key,
            default_model=default_model,
            timeout=timeout,
            max_retries=max_retries,
            model_aliases=model_aliases
        )
        
        self.organization = organization
    
    @property
    def name(self) -> str:
        return "openai"
    
    @property
    def models(self) -> List[str]:
        return [
            "gpt-4o-mini",
            "gpt-4o",
            "gpt-4-turbo",
            "gpt-4",
            "gpt-3.5-turbo"
        ]
    
    def get_headers(self) -> Dict[str, str]:
        """获取请求头"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "GovBudgetChecker/1.0"
        }
        
        if self.organization:
            headers["OpenAI-Organization"] = self.organization
        
        return headers
    
    def format_messages(self, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """格式化消息，OpenAI 格式"""
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
                "temperature": min(max(temperature, 0.0), 2.0),  # OpenAI 支持 0-2
                "stream": False
            }
            
            if max_tokens:
                payload["max_tokens"] = max_tokens
            
            # 添加其他参数
            if "top_p" in kwargs:
                payload["top_p"] = kwargs["top_p"]
            if "frequency_penalty" in kwargs:
                payload["frequency_penalty"] = kwargs["frequency_penalty"]
            if "presence_penalty" in kwargs:
                payload["presence_penalty"] = kwargs["presence_penalty"]
            if "stop" in kwargs:
                payload["stop"] = kwargs["stop"]
            if "logit_bias" in kwargs:
                payload["logit_bias"] = kwargs["logit_bias"]
            if "user" in kwargs:
                payload["user"] = kwargs["user"]
            
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
            "temperature": min(max(temperature, 0.0), 2.0),
            "stream": True
        }
        
        if max_tokens:
            payload["max_tokens"] = max_tokens
        
        # 添加其他参数
        for key in ["top_p", "frequency_penalty", "presence_penalty", "stop", "user"]:
            if key in kwargs:
                payload[key] = kwargs[key]
        
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
        """解析 OpenAI 特定的错误响应"""
        error_info = response_data.get('error', {})
        error_code = error_info.get('code', '')
        error_msg = error_info.get('message', 'Unknown error')
        error_type_str = error_info.get('type', '')
        
        # OpenAI 特定错误码映射
        if error_type_str == 'invalid_api_key' or error_code == 'invalid_api_key' or status_code in [401, 403]:
            error_type = LLMErrorType.AUTHENTICATION
        elif error_type_str == 'model_not_found' or 'model' in error_msg.lower() and 'does not exist' in error_msg.lower() or status_code == 404:
            error_type = LLMErrorType.MODEL_NOT_FOUND
        elif error_type_str == 'rate_limit_exceeded' or error_code == 'rate_limit_exceeded' or status_code == 429:
            error_type = LLMErrorType.RATE_LIMIT
        elif error_type_str == 'quota_exceeded' or 'quota' in error_msg.lower():
            error_type = LLMErrorType.QUOTA_EXCEEDED
        elif error_type_str == 'timeout' or status_code == 408:
            error_type = LLMErrorType.TIMEOUT
        elif 500 <= status_code < 600:
            error_type = LLMErrorType.SERVER_ERROR
        elif status_code == 400:
            error_type = LLMErrorType.INVALID_REQUEST
        else:
            error_type = LLMErrorType.UNKNOWN
        
        # 提取重试等待时间
        retry_after = None
        if error_type == LLMErrorType.RATE_LIMIT:
            # 尝试从错误消息中提取重试时间
            import re
            match = re.search(r'try again in (\d+)s', error_msg)
            if match:
                retry_after = int(match.group(1))
        
        return LLMError(
            error_type=error_type,
            message=f"OpenAI API Error: {error_msg}",
            status_code=status_code,
            provider=self.name,
            retry_after=retry_after
        )