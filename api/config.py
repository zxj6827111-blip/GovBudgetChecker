"""Configuration helpers for the FastAPI service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_RULES_FILE = Path(os.getenv("RULES_FILE", "rules/v3_3.yaml"))
# Cross-platform string representation (always uses forward slashes for consistency)
DEFAULT_RULES_FILE_STR = DEFAULT_RULES_FILE.as_posix()

# AI辅助配置
# 默认关闭 AI；保存界面配置后，以界面开关为准，否则兼容环境变量。
AI_ASSIST_ENABLED = os.getenv("AI_ASSIST_ENABLED", "false").lower() == "true"
AI_EXTRACTOR_URL = os.getenv("AI_EXTRACTOR_URL", "http://127.0.0.1:9009/ai/extract/v1")


@dataclass(frozen=True)
class AppConfig:
    """Runtime configuration for the API service."""

    rules_file: Path = DEFAULT_RULES_FILE
    ai_assist_enabled: bool = AI_ASSIST_ENABLED
    ai_extractor_url: str = AI_EXTRACTOR_URL

    @classmethod
    def load(cls) -> "AppConfig":
        """Load configuration from environment variables."""

        rules_file = Path(os.getenv("RULES_FILE", str(DEFAULT_RULES_FILE)))
        from src.services.ai_model_settings import capabilities
        ai_assist_enabled = capabilities()["enabled"]
        ai_extractor_url = os.getenv("AI_EXTRACTOR_URL", "http://127.0.0.1:9009/ai/extract/v1")
        
        return cls(
            rules_file=rules_file,
            ai_assist_enabled=ai_assist_enabled,
            ai_extractor_url=ai_extractor_url
        )
