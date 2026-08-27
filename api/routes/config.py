"""Configuration endpoint."""

from __future__ import annotations

import os

from fastapi import APIRouter

from api import runtime
from api import queue_runtime

router = APIRouter()


@router.get("/config")
@router.get("/api/config")
async def get_config():
    ai_enabled = os.getenv("AI_ASSIST_ENABLED", "true").lower() == "true"
    ai_extractor_url = os.getenv("AI_EXTRACTOR_URL", "http://127.0.0.1:9009/ai/extract/v1")
    queue_enabled = queue_runtime.queue_enabled()
    queue_role = queue_runtime.get_queue_role()
    return {
        "ai_enabled": ai_enabled,
        "ai_assist_enabled": ai_enabled,
        "ai_extractor_url": ai_extractor_url,
        "auth_enabled": bool(runtime.security_config.enabled) if runtime.security_config else False,
        "queue_enabled": queue_enabled,
        "queue_role": queue_role,
        "queue_inline_fallback_enabled": queue_runtime.allow_inline_fallback(),
        "local_queue_expected": queue_enabled and queue_role in {"all", "worker"},
        # UI 重建第二批 Task 5：上传中心必须显示系统真实的上传限制，而不是原型图上
        # 的设计稿占位值（"单个文件不超过 200 MB"）。真实默认值是 30MB/800页
        # （见 api/runtime.py 的 MAX_UPLOAD_MB / MAX_UPLOAD_PAGES），照抄 200MB
        # 会让用户按提示传 100MB 文件却被拒绝，是直接的用户伤害。
        "max_upload_mb": runtime.MAX_UPLOAD_MB,
        "max_upload_pages": runtime.MAX_UPLOAD_PAGES,
    }
