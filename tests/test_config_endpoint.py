"""UI 重建第二批 Task 5：`/api/config` 必须暴露真实的上传限制。

背景：上传中心原型图写"单个文件不超过 200 MB"，但系统 `MAX_UPLOAD_MB` 默认是
30。前端必须从这个端点读真实值显示，不能照抄设计稿的 200MB——否则用户按
提示传 100MB 文件会被直接拒绝，是可验证的用户伤害。这里只测端点契约本身，
真实的"前端确实用了这个值而不是硬编码"由 app/tests/uploadCenterAdapters.test.ts
断言（跨语言分两处测，覆盖后端契约与前端消费两侧）。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.routes.config as config_route


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(config_route.router)
    return TestClient(app)


def test_config_exposes_real_upload_limits(monkeypatch):
    """反例核心：必须是 runtime.py 里的真实值，不能被前端另抄一份 200。"""
    monkeypatch.setattr(config_route.runtime, "MAX_UPLOAD_MB", 30)
    monkeypatch.setattr(config_route.runtime, "MAX_UPLOAD_PAGES", 800)

    response = _client().get("/api/config")
    assert response.status_code == 200
    payload = response.json()

    assert payload["max_upload_mb"] == 30
    assert payload["max_upload_pages"] == 800
    # 原型图占位值 200 绝不能出现在默认配置里（真实默认值是 30）
    assert payload["max_upload_mb"] != 200


def test_config_upload_limits_follow_runtime_overrides(monkeypatch):
    """当运维通过环境变量改了限制（runtime.py 已读出新值）时，端点必须原样透传，
    不能缓存或返回编译期常量——否则运维改配置后前端提示与后端真实行为不一致。
    """
    monkeypatch.setattr(config_route.runtime, "MAX_UPLOAD_MB", 50)
    monkeypatch.setattr(config_route.runtime, "MAX_UPLOAD_PAGES", 1200)

    response = _client().get("/api/config")
    payload = response.json()

    assert payload["max_upload_mb"] == 50
    assert payload["max_upload_pages"] == 1200
