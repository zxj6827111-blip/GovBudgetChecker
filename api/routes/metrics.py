"""关键指标端点（缺口 B-06）。

鉴权策略（不得无鉴权暴露内部指标）
--------------------------------
两条路，二者满足其一即可：

1. **管理员会话**：`X-Session-Token` 对应的用户必须是管理员。
   沿用 `/ready?details=true` 的既有先例（`api/routes/health.py:_include_ready_details`）。
2. **独立抓取令牌**：配置 `METRICS_API_TOKEN` 后，采集器可用
   `X-Metrics-Token`（或 `Authorization: Bearer <token>`）抓取。
   给采集器单独发令牌，避免把管理员口令配到 Prometheus 里。

另外本路由**不在** `SecurityConfig.exempt_paths` 里，所以 API Key 中间件的鉴权
与限流照旧生效——也就是说至少要过两道：API Key + （管理员会话 或 抓取令牌）。

`METRICS_ENABLED=false` 可以整体关闭端点（返回 404），用于"只走日志聚合"的部署。
"""

from __future__ import annotations

import hmac
import os
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request, Response

from api import runtime
from api.auth_utils import require_admin
from src.services.metrics import collect_metrics_cached, render_prometheus

router = APIRouter()

_METRICS_TOKEN_HEADER = "X-Metrics-Token"


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def metrics_enabled() -> bool:
    return _env_flag("METRICS_ENABLED", True)


def _configured_token() -> str:
    return str(os.getenv("METRICS_API_TOKEN", "") or "").strip()


def _presented_token(request: Request) -> str:
    token = str(request.headers.get(_METRICS_TOKEN_HEADER) or "").strip()
    if token:
        return token
    auth_header = str(request.headers.get("Authorization") or "")
    if auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1].strip()
    return ""


def authorize_metrics_request(request: Request) -> str:
    """校验抓取权限，返回通过的方式（`scrape_token` / `admin_session`）。

    先看抓取令牌：采集器没有会话，令牌是它唯一可用的凭据。
    令牌未配置或不匹配时回落到管理员会话校验（`require_admin` 会抛 401/403）。
    """
    configured = _configured_token()
    presented = _presented_token(request)
    if configured and presented and hmac.compare_digest(configured, presented):
        return "scrape_token"
    require_admin(request)
    return "admin_session"


@router.get("/metrics")
@router.get("/api/metrics")
async def metrics(request: Request, response: Response) -> Any:
    if not metrics_enabled():
        raise HTTPException(status_code=404, detail="metrics endpoint is disabled")

    auth_mode = authorize_metrics_request(request)
    payload: Dict[str, Any] = collect_metrics_cached(runtime.UPLOAD_ROOT)

    fmt = str(request.query_params.get("format") or "json").strip().lower()
    if fmt in {"prometheus", "prom", "text"}:
        return Response(
            content=render_prometheus(payload),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    result = dict(payload)
    result["auth_mode"] = auth_mode
    return result
