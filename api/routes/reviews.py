"""人工复核生命周期 API（WP3-A）。

接口面
------
    1. ``GET  /api/reviews/{slot_id}``         槽位的当前复核状态与完成门禁
    2. ``GET  /api/reviews?job_uuid=...``      按任务解析复核上下文（工作台入口）
    3. ``POST /api/reviews/{slot_id}/start``   开始（或复用）复核会话
    4. ``POST /api/reviews/{slot_id}/complete`` 完成复核（服务端门禁）
    5. ``POST /api/reviews/{slot_id}/reopen``  显式重新复核

第 2 条与第 1 条是同一个"读当前复核"的两种取址方式，不是第二套逻辑：
审核工作台的入口仍然是 ``/review?job=<uuid>``，页面只知道 job_uuid，
必须有一次"job → 槽位"的服务端解析才能拿到 slot_id。让服务端做这次解析
（而不是把 slot_id 塞进 URL 由前端拼接），正是 §二十五 那条纪律的落点：
只认 ``structured_ingest.document_version_id`` 这条精确链路，
按文件名/组织名/时间接近猜归属一律不做。

四条纪律
--------
1. **全部服务端鉴权，先槽位后任务。** 复核的目标资源是槽位：先判槽位可访问
   （不存在 404 / 越权 403），再判这次复核对应的 job 可访问。两个判断不能
   合成一个——"能看这条材料"和"能看这次运行"是两个不同的授权面（§六十七）。
2. **不信任前端。** 请求体里的 ``job_uuid`` 只被当作"待核对的声明"：
   服务端自行验证它属于 URL 里的槽位且正是当前分析，不匹配 409（§二十八/§七十一）。
3. **错误沿用仓库既有契约。** 404 不存在、403 无权、409 业务状态不允许、
   422 参数问题、503 数据库不可用；业务 409 的 ``detail`` 是对象，
   带 ``error`` 与 ``blockers``，不是一句字符串（§一百零五）。
4. **读接口不做写操作。** ``GET`` 只读；自动开始复核由前端在确认上下文后
   显式调用 ``start``（幂等），服务端不在 GET 里偷偷建会话——那会让
   "有人浏览过这条材料"变成一条持久化事实。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Awaitable, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request

from api import material_access, runtime
from api.auth_utils import require_login, user_can_access_job
from src.schemas.review_lifecycle import (
    ReviewActionRequest,
    ReviewByJobResponse,
    ReviewLifecycleResponse,
    ReviewMutationResponse,
)
from src.services import review_lifecycle_service
from src.services.audit_log import append_audit_event
from src.services.material_detail_query_service import MaterialDetailQueryService

logger = logging.getLogger(__name__)

router = APIRouter()


async def _open_connection() -> Any:
    return await material_access.open_connection()


async def _close_connection(conn: Any) -> None:
    await material_access.close_connection(conn)


async def _with_connection(callback: Callable[[Any], Awaitable[Any]]) -> Any:
    """材料台账与复核接口共用同一套"取连接 / 503 口径 / 归还"。

    测试可以替换本模块的 ``_open_connection`` 注入假连接：接口契约
    （鉴权、错误码、字段名）因此能在无真库时被完整验证。
    """
    return await material_access.with_connection(
        callback,
        opener=lambda: _open_connection(),
        closer=lambda conn: _close_connection(conn),
    )


def _job_access_ok(user: Any, job_uuid: str) -> bool:
    """该账号能否读到这次运行的详情。

    读不到 payload（任务目录不存在、状态文件损坏）一律按"不可访问"处理：
    复核结论会引用这次运行的发现，能复核却看不到运行的账号不该被允许做决定。
    """
    normalized = str(job_uuid or "").strip()
    if not normalized:
        return True
    try:
        payload = runtime.get_job_status_payload(normalized)
    except HTTPException:
        return False
    return user_can_access_job(user, payload)


async def _load_slot_row(conn: Any, slot_id: str) -> Optional[dict]:
    return await MaterialDetailQueryService(conn).load_slot_row(slot_id)


def _require_job_access(user: Any, job_uuid: Optional[str]) -> None:
    if not _job_access_ok(user, job_uuid or ""):
        raise HTTPException(status_code=403, detail="job access denied")


def _actor(user: Any) -> str:
    return str(user.get("username") or "").strip() or "unknown"


# ---- 读 ---------------------------------------------------------------------


@router.get("/api/reviews", response_model=ReviewByJobResponse)
async def get_review_by_job(
    request: Request,
    job_uuid: str = Query(..., min_length=1, description="分析任务 id"),
) -> ReviewByJobResponse:
    """按任务解析可持久化的复核上下文（审核工作台入口）。

    没有槽位链路（legacy 未关联任务）时返回 200 + ``review_context.available=false``
    + ``reason=review_context_unavailable``，而不是 409：那**不是错误状态**，
    而是"这条历史任务无法建立材料复核上下文"——页面照样能看旧审核内容，
    只是不能点「完成复核」（§七十三）。开/完成接口遇到同样情形才返回 409。
    """
    _, _, user = require_login(request)
    if not _job_access_ok(user, job_uuid):
        raise HTTPException(status_code=403, detail="job access denied")

    data = await _with_connection(
        lambda conn: review_lifecycle_service.load_review_by_job(conn, job_uuid)
    )
    # 上下文可解析时再判一次"槽位可访问"，避免"有权看任务、无权看材料"的账号
    # 通过任务 id 读到材料的复核结论（反向 IDOR）。
    if data.review_context.available and data.review is not None:
        await _require_slot_readable(user, data.review.slot_id)
    return ReviewByJobResponse(ok=True, data=data)


async def _require_slot_readable(user: Any, slot_id: str) -> None:
    """槽位可访问性判定（与材料详情同一个入口，不存在 404 / 越权 403）。"""
    scope = material_access.build_access_scope(user)

    async def _load(conn: Any) -> Optional[dict]:
        return material_access.require_slot_access(
            scope, await _load_slot_row(conn, slot_id), slot_id
        )

    await _with_connection(_load)


@router.get("/api/reviews/{slot_id}", response_model=ReviewLifecycleResponse)
async def get_review(
    slot_id: str,
    request: Request,
    job_uuid: Optional[str] = Query(default=None, description="锁定到具体一次运行"),
) -> ReviewLifecycleResponse:
    """槽位的复核状态、当前分析、会话历史与完成门禁。

    ``completion_gate`` 是**服务端重算**的结论，前端不得据此之外的规则自行判断。
    界面上的按钮 disabled 只是即时提示：真正的判定永远在这里（§七十八）。
    """
    _, _, user = require_login(request)
    await _require_slot_readable(user, slot_id)

    async def _load(conn: Any) -> Any:
        return await review_lifecycle_service.load_review_by_slot(
            conn, slot_id, expected_job_uuid=job_uuid
        )

    data = await _with_connection(_load)
    _require_job_access(user, data.current_analysis.job_uuid)
    return ReviewLifecycleResponse(ok=True, data=data)


# ---- 写 ---------------------------------------------------------------------


@router.post("/api/reviews/{slot_id}/start", response_model=ReviewMutationResponse)
async def start_review(
    slot_id: str,
    request: Request,
    payload: Optional[ReviewActionRequest] = Body(default=None),
) -> ReviewMutationResponse:
    """开始复核（幂等）。

    同一 (槽位, 版本, 分析代际) 已有进行中的会话时返回**同一条**会话：
    两个浏览器同时点进来也只能有一条活动复核（数据库的 partial unique index
    是最终保证，应用层的先查再写只是让正常路径不报错）。

    进入工作台可以自动/幂等调用本接口，但**绝不自动完成**（§三十三）。
    """
    return await _mutate(slot_id, request, payload, action="start")


@router.post("/api/reviews/{slot_id}/complete", response_model=ReviewMutationResponse)
async def complete_review(
    slot_id: str,
    request: Request,
    payload: Optional[ReviewActionRequest] = Body(default=None),
) -> ReviewMutationResponse:
    """完成复核：服务端重算全部门禁，任一不成立即 409 + 业务原因。

    幂等：对同一条已完成会话重复调用返回同一个结果，不新建第二条完成记录、
    不重复写审计与槽位状态。
    """
    return await _mutate(slot_id, request, payload, action="complete")


@router.post("/api/reviews/{slot_id}/reopen", response_model=ReviewMutationResponse)
async def reopen_review(
    slot_id: str,
    request: Request,
    payload: Optional[ReviewActionRequest] = Body(default=None),
) -> ReviewMutationResponse:
    """重新复核：旧 completed 会话转 ``invalidated``，另起一条新会话。

    这是"已完成复核之后再改问题"的唯一合法前置动作（问题编辑锁由复核完成时落下）。
    """
    return await _mutate(slot_id, request, payload, action="reopen")


async def _mutate(
    slot_id: str,
    request: Request,
    payload: Optional[ReviewActionRequest],
    *,
    action: str,
) -> ReviewMutationResponse:
    """三个写动作共用的前置：鉴权 → 校验 → 调用服务 → 审计。"""
    _, _, user = require_login(request)
    await _require_slot_readable(user, slot_id)

    job_uuid = (payload.job_uuid if payload is not None else None) or None
    if job_uuid:
        _require_job_access(user, job_uuid)

    actor = _actor(user)
    handler = {
        "start": review_lifecycle_service.start_review,
        "complete": review_lifecycle_service.complete_review,
        "reopen": review_lifecycle_service.reopen_review,
    }[action]

    try:
        data = await _with_connection(
            lambda conn: handler(conn, slot_id, actor=actor, job_uuid=job_uuid)
        )
    except review_lifecycle_service.ReviewLifecycleError as exc:
        # 被门禁拒绝也要留痕：审计关心的是"谁在什么时候尝试过什么"，
        # 只记成功的动作会让"为什么这份材料一直完不成"无从追查。
        append_audit_event(
            action=f"review.{action}",
            actor=actor,
            result="rejected",
            resource_type="review_session",
            resource_id=slot_id,
            details={
                "slot_id": slot_id,
                "error": exc.error,
                "blocker_codes": [item.code for item in exc.blockers],
            },
        )
        raise

    append_audit_event(
        action=f"review.{action}",
        actor=actor,
        result="success",
        resource_type="review_session",
        resource_id=data.session.review_session_id,
        details={
            "slot_id": slot_id,
            "document_version_id": data.session.document_version_id,
            "analysis_job_uuid": data.session.analysis_job_uuid,
            "status": data.session.status,
        },
    )
    return ReviewMutationResponse(ok=True, data=data)


__all__ = ["router"]
