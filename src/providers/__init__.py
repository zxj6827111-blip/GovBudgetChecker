"""
AI 提供商统一接口包
支持多提供商容灾回退：Zhipu → Doubao → OpenAI 兼容等
"""
from .base import LLMProvider, LLMResponse, LLMError, LLMErrorType
from .zhipu import ZhipuProvider
from .doubao import DoubaoProvider
from .openai_compat import OpenAICompatProvider

__all__ = [
    'LLMProvider',
    'LLMResponse', 
    'LLMError',
    'LLMErrorType',
    'ZhipuProvider',
    'DoubaoProvider',
    'OpenAICompatProvider'
]