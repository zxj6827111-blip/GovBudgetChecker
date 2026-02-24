"""
AI 提供商基础接口定义
统一接口、认证、参数默认值
"""
from typing import Protocol, Dict, List, Any, Optional, AsyncIterator
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
import time


class LLMErrorType(Enum):
    """LLM 错误类型"""
    AUTHENTICATION = "authentication"  # 401/403
    MODEL_NOT_FOUND = "model_not_found"  # 404
    RATE_LIMIT = "rate_limit"  # 429
    TIMEOUT = "timeout"  # 408/超时
    SERVER_ERROR = "server_error"  # 5xx
    NETWORK_ERROR = "network_error"  # 网络错误
    INVALID_REQUEST = "invalid_request"  # 400
    QUOTA_EXCEEDED = "quota_exceeded"  # 配额超限
    UNKNOWN = "unknown"


@dataclass
class LLMError(Exception):
    """LLM 错误"""
    error_type: LLMErrorType
    message: str
    status_code: Optional[int] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    retry_after: Optional[int] = None  # 重试等待时间（秒）
    
    def __str__(self):
        return f"LLMError({self.error_type.value}): {self.message}"
    
    @property
    def is_retryable(self) -> bool:
        """是否可重试"""
        return self.error_type in {
            LLMErrorType.RATE_LIMIT,
            LLMErrorType.TIMEOUT,
            LLMErrorType.SERVER_ERROR,
            LLMErrorType.NETWORK_ERROR
        }
    
    @property
    def should_fallback(self) -> bool:
        """是否应该切换提供商"""
        return self.error_type in {
            LLMErrorType.AUTHENTICATION,
            LLMErrorType.MODEL_NOT_FOUND,
            LLMErrorType.QUOTA_EXCEEDED
        }


@dataclass
class LLMResponse:
    """LLM 响应"""
    content: str
    model: str
    provider: str
    usage: Dict[str, Any]
    latency_ms: int
    finish_reason: str = "stop"
    
    # 元数据
    request_id: Optional[str] = None
    created_at: float = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = time.time()


class LLMProvider(Protocol):
    """LLM 提供商统一接口"""
    
    @property
    def name(self) -> str:
        """提供商名称"""
        ...
    
    @property
    def models(self) -> List[str]:
        """支持的模型列表"""
        ...
    
    async def chat(self, 
                   messages: List[Dict[str, str]], 
                   model: Optional[str] = None,
                   temperature: float = 0.2,
                   max_tokens: Optional[int] = None,
                   timeout: int = 60,
                   **kwargs) -> LLMResponse:
        """聊天接口"""
        ...
    
    async def chat_stream(self, 
                         messages: List[Dict[str, str]], 
                         model: Optional[str] = None,
                         temperature: float = 0.2,
                         max_tokens: Optional[int] = None,
                         timeout: int = 60,
                         **kwargs) -> AsyncIterator[Dict[str, Any]]:
        """流式聊天接口"""
        ...
    
    def is_available(self) -> bool:
        """检查提供商是否可用"""
        ...
    
    async def health_check(self) -> Dict[str, Any]:
        """健康检查"""
        ...


class BaseLLMProvider(ABC):
    """LLM 提供商基础实现"""
    
    def __init__(self, 
                 base_url: str,
                 api_key: str,
                 default_model: str,
                 timeout: int = 60,
                 max_retries: int = 1,
                 model_aliases: Optional[Dict[str, str]] = None):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.default_model = default_model
        self.timeout = timeout
        self.max_retries = max_retries
        self.model_aliases = model_aliases or {}
        
        # 统计信息
        self.request_count = 0
        self.error_count = 0
        self.total_latency = 0
        self.last_request_time = 0
    
    @property
    @abstractmethod
    def name(self) -> str:
        """提供商名称"""
        pass
    
    @property
    @abstractmethod
    def models(self) -> List[str]:
        """支持的模型列表"""
        pass
    
    def resolve_model(self, model: Optional[str] = None) -> str:
        """解析模型名称，支持别名映射"""
        target_model = model or self.default_model
        return self.model_aliases.get(target_model, target_model)
    
    def get_headers(self) -> Dict[str, str]:
        """获取请求头"""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "GovBudgetChecker/1.0"
        }
    
    def format_messages(self, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """格式化消息，子类可重写"""
        return messages
    
    def parse_error(self, status_code: int, response_data: Dict[str, Any]) -> LLMError:
        """解析错误响应"""
        error_msg = response_data.get('error', {}).get('message', 'Unknown error')
        
        if status_code == 401 or status_code == 403:
            error_type = LLMErrorType.AUTHENTICATION
        elif status_code == 404:
            error_type = LLMErrorType.MODEL_NOT_FOUND
        elif status_code == 429:
            error_type = LLMErrorType.RATE_LIMIT
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
            message=error_msg,
            status_code=status_code,
            provider=self.name
        )
    
    def update_stats(self, latency_ms: int, success: bool = True):
        """更新统计信息"""
        self.request_count += 1
        self.total_latency += latency_ms
        self.last_request_time = time.time()
        
        if not success:
            self.error_count += 1
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        avg_latency = self.total_latency / max(self.request_count, 1)
        error_rate = self.error_count / max(self.request_count, 1)
        
        return {
            "provider": self.name,
            "request_count": self.request_count,
            "error_count": self.error_count,
            "error_rate": error_rate,
            "avg_latency_ms": avg_latency,
            "last_request_time": self.last_request_time
        }
    
    def is_available(self) -> bool:
        """检查提供商是否可用"""
        return bool(self.api_key and self.base_url)
    
    async def health_check(self) -> Dict[str, Any]:
        """健康检查"""
        try:
            # 发送简单的测试请求
            test_messages = [{"role": "user", "content": "Hello"}]
            response = await self.chat(
                messages=test_messages,
                max_tokens=10,
                timeout=10
            )
            
            return {
                "status": "healthy",
                "provider": self.name,
                "model": response.model,
                "latency_ms": response.latency_ms,
                "timestamp": time.time()
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "provider": self.name,
                "error": str(e),
                "timestamp": time.time()
            }
    
    @abstractmethod
    async def chat(self, 
                   messages: List[Dict[str, str]], 
                   model: Optional[str] = None,
                   temperature: float = 0.2,
                   max_tokens: Optional[int] = None,
                   timeout: int = 60,
                   **kwargs) -> LLMResponse:
        """聊天接口实现"""
        pass
    
    @abstractmethod
    async def chat_stream(self, 
                         messages: List[Dict[str, str]], 
                         model: Optional[str] = None,
                         temperature: float = 0.2,
                         max_tokens: Optional[int] = None,
                         timeout: int = 60,
                         **kwargs) -> AsyncIterator[Dict[str, Any]]:
        """流式聊天接口实现"""
        pass


def mask_api_key(api_key: str) -> str:
    """掩码 API Key，只显示前4后3位"""
    if not api_key or len(api_key) < 8:
        return "***"
    return f"{api_key[:4]}***{api_key[-3:]}"


def classify_http_error(status_code: int) -> LLMErrorType:
    """根据 HTTP 状态码分类错误"""
    if status_code in [401, 403]:
        return LLMErrorType.AUTHENTICATION
    elif status_code == 404:
        return LLMErrorType.MODEL_NOT_FOUND
    elif status_code == 429:
        return LLMErrorType.RATE_LIMIT
    elif status_code == 408:
        return LLMErrorType.TIMEOUT
    elif 500 <= status_code < 600:
        return LLMErrorType.SERVER_ERROR
    elif status_code == 400:
        return LLMErrorType.INVALID_REQUEST
    else:
        return LLMErrorType.UNKNOWN