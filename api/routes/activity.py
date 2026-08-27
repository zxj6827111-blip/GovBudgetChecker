"""最近活动流只读接口（Task 3）。

基于既有 `src/services/audit_log.py` 的审计事件文件暴露只读分页 API，
供原型图工作台总览"最近活动"面板消费。

鉴权
----
沿用 `/metrics`、`/ready?details=true` 的既有先例：管理员会话。
审计事件本身就是"谁在什么时候做了什么管理操作"，属于敏感的内部运维信息，
不应该让普通审核员账号也能看到别人的操作记录，因此直接用 `require_admin`
而不是 `require_login`——这比 `/metrics` 更严格是有意的（`/metrics` 还有
独立抓取令牌这条路给采集器用，活动流没有这种"非人类调用方"的场景，
没必要为它开一条平行的鉴权通道）。

脱敏
----
必须复用现有日志脱敏能力（`src.utils.logging_config.redact_log_fields`），
不得让材料原文或凭据经此接口泄漏。审计事件的 `details` 字段是各调用点
自由传入的字典（例如 `jobs.py` 的批量删除会传 `requested_count` 这类纯数字，
但不能保证未来所有调用点都只传安全字段），因此在 API 层统一对每条记录的
`details` 做一次 `redact_log_fields`，与写入日志时的脱敏是同一套判定规则
（`SENSITIVE_LOG_KEYS` / `is_sensitive_log_key`），保证"防线只有一套标准"
而不是"读的时候另写一套脱敏逻辑，两套判定可能不一致"。
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Query, Request

from api.auth_utils import require_admin
from src.services.audit_log import read_audit_events
from src.utils.logging_config import redact_log_fields

router = APIRouter()


def _redact_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """对单条审计事件做脱敏：只处理 `details`，其余字段（action/actor/ts 等）
    是结构化元数据本身，不是"可能夹带原文"的自由字段，不需要过一遍脱敏。
    """
    redacted = dict(event)
    details = event.get("details")
    if isinstance(details, dict):
        redacted["details"] = redact_log_fields(details)
    return redacted


@router.get("/api/activity")
async def list_activity_events(
    request: Request,
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Dict[str, Any]:
    require_admin(request)
    result = read_audit_events(limit=limit, offset=offset)
    result["items"] = [_redact_event(event) for event in result["items"]]
    return result
