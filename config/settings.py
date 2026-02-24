"""
配置加载器
支持从 YAML 文件和环境变量加载配置
"""
import os
import yaml
from typing import Dict, Any, Optional
from pathlib import Path

from src.schemas.issues import AnalysisConfig


class Settings:
    """配置管理器"""
    
    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path or self._get_default_config_path()
        self._config = {}
        self._load_config()
    
    def _get_default_config_path(self) -> str:
        """获取默认配置文件路径"""
        current_dir = Path(__file__).parent
        return str(current_dir / "app.yaml")
    
    def _load_config(self):
        """加载配置"""
        # 1. 加载YAML配置
        if os.path.exists(self.config_path):
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self._config = yaml.safe_load(f) or {}
        
        # 2. 环境变量覆盖
        self._load_env_overrides()
    
    def _load_env_overrides(self):
        """加载环境变量覆盖"""
        env_mappings = {
            # 双模式开关
            "DUAL_MODE": ("dual_mode", "enabled"),
            "AI_ENABLED": ("dual_mode", "ai_enabled"),
            "RULE_ENABLED": ("dual_mode", "rule_enabled"),
            "MERGE_ENABLED": ("dual_mode", "merge_enabled"),
            
            # AI配置
            "AI_PROVIDER": ("ai", "provider"),
            "AI_MODEL": ("ai", "model"),
            "AI_BASE_URL": ("ai", "base_url"),
            "AI_TIMEOUT": ("ai", "timeout_seconds"),
            "AI_RETRY": ("ai", "retry_count"),
            "AI_TEMPERATURE": ("ai", "temperature"),
            "AI_MAX_TOKENS": ("ai", "max_tokens"),
            
            # OpenAI兼容配置
            "OPENAI_API_KEY": ("ai", "api_key"),
            "OPENAI_BASE_URL": ("ai", "base_url"),
            "OPENAI_MODEL": ("ai", "model"),
            
            # 合并配置
            "MERGE_TITLE_SIM": ("merge", "title_similarity_threshold"),
            "MERGE_MONEY_TOL": ("merge", "money_tolerance"),
            "MERGE_PCT_TOL": ("merge", "percentage_tolerance"),
            "MERGE_PAGE_TOL": ("merge", "page_tolerance"),
            
            # 日志配置
            "LOG_LEVEL": ("logging", "level"),
            "LOG_FILE": ("logging", "file"),
        }
        
        for env_key, (section, key) in env_mappings.items():
            env_value = os.getenv(env_key)
            if env_value is not None:
                if section not in self._config:
                    self._config[section] = {}
                
                # 类型转换
                self._config[section][key] = self._convert_env_value(env_value)
    
    def _convert_env_value(self, value: str) -> Any:
        """转换环境变量值"""
        # 布尔值
        if value.lower() in ('true', 'false'):
            return value.lower() == 'true'
        
        # 数字
        try:
            if '.' in value:
                return float(value)
            else:
                return int(value)
        except ValueError:
            pass
        
        # 字符串
        return value
    
    def get(self, section: str, key: str, default: Any = None) -> Any:
        """获取配置值"""
        return self._config.get(section, {}).get(key, default)
    
    def get_section(self, section: str) -> Dict[str, Any]:
        """获取配置段"""
        return self._config.get(section, {})
    
    def to_analysis_config(self) -> AnalysisConfig:
        """转换为分析配置"""
        dual_mode_config = self.get_section("dual_mode")
        ai_config = self.get_section("ai")
        merge_config = self.get_section("merge")
        
        return AnalysisConfig(
            dual_mode=dual_mode_config.get("enabled", True),
            ai_enabled=dual_mode_config.get("ai_enabled", True),
            rule_enabled=dual_mode_config.get("rule_enabled", True),
            merge_enabled=dual_mode_config.get("merge_enabled", True),
            
            title_similarity_threshold=merge_config.get("title_similarity_threshold", 0.85),
            money_tolerance=merge_config.get("money_tolerance", 0.005),
            percentage_tolerance=merge_config.get("percentage_tolerance", 0.002),
            page_tolerance=merge_config.get("page_tolerance", 1),
            
            ai_timeout=ai_config.get("timeout_seconds", 60),
            ai_retry=ai_config.get("retry_count", 1),
            ai_temperature=ai_config.get("temperature", 0.2)
        )
    
    def is_dual_mode_enabled(self) -> bool:
        """是否启用双模式"""
        return self.get("dual_mode", "enabled", True)
    
    def get_ai_config(self) -> Dict[str, Any]:
        """获取AI配置"""
        config = self.get_section("ai")
        
        # 兼容现有环境变量
        if "api_key" not in config:
            config["api_key"] = os.getenv("OPENAI_API_KEY", "")
        
        return config
    
    def get_logging_config(self) -> Dict[str, Any]:
        """获取日志配置"""
        return self.get_section("logging")


# 全局配置实例
_settings = None

def get_settings() -> Settings:
    """获取全局配置实例"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings

def reload_settings():
    """重新加载配置"""
    global _settings
    _settings = None
    return get_settings()
