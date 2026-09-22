"""分析落库后的槽位复核状态同步：把 WP1 预置的复核三态真正接通。

问题是什么
----------
WP1 的状态机早就定义了 ``review_required`` / ``reviewing`` / ``completed``，
但**没有任何路径会写出它们**：``upsert_from_decision`` 只会在结构化入库那一步
（分析还在跑）把状态写成"已上传待分析"，分析真正跑完之后没有人再动过槽位状态。
结果是台账永远停留在 ``uploaded``，"待人工复核"这件事在台账上完全不可见。

本模块补上这一步：一次分析**结果落库**之后，按事实重算槽位状态。

为什么不在复核服务里做
----------------------
本模块被 ``analysis_result_store`` 调用，而复核服务依赖 ``issue_workflow_store``、
后者又依赖 ``api.runtime``。若把这段逻辑放进复核服务，
``analysis_result_store → 复核服务 → issue_workflow_store → api.runtime``
就构成循环导入。本模块只依赖"上下文解析 + 会话存储 + 槽位服务"三个底层模块，
因此可以直接被落库路径引用。

四条不可让步的口径
------------------
1. **只对"当前分析"生效。** 落库的这次运行必须正是槽位当前版本上的当前分析；
   一次历史运行（旧版本，或同版本上已被更新运行取代）落库不允许改写槽位状态——
   那会让"某次历史重放"把当前材料的复核状态改掉。
2. **已完成复核只在同一分析代际上才算数。** 检查"存在 completed 会话"时必须带上
   ``analysis_basis_token``：跨代际沿用等于拿上一代结论冒充这一代。
3. **不修状态机。** 状态值一律由 ``MaterialSlotService.refresh_status`` 推导，
   本模块只提供 ``analysis_state`` / ``review_state`` 两个输入。
4. **失败不得阻断分析落库。** 槽位是旁路能力，其故障不允许让"分析结果写库"失败
   （与 ``safe_allocate_for_document`` 同一条纪律）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from src.services import review_context_query, review_session_store
from src.services.material_slot_service import MaterialSlotService
from src.services.material_slot_status import (
    ANALYSIS_DONE,
    REVIEW_COMPLETED,
    REVIEW_REQUIRED,
)

logger = logging.getLogger(__name__)


async def sync_slot_review_state_for_job(
    conn: Any, job_uuid: str
) -> Optional[Dict[str, Any]]:
    """一次分析结果落库后，重算它所属槽位的状态。

    返回 ``{"slot_id", "status", "status_reason"}``；不满足生效条件时返回 ``None``
    （不是错误：多数落库事件本来就不该改槽位状态）。

    调用方必须是"结果刚刚落库"的路径（``include_results=True``）。
    进度更新、metadata 修复都不应该走这里：它们不改变"分析走到哪一步"。
    """
    link = await review_context_query.resolve_slot_link_for_job(conn, job_uuid)
    if link is None:
        return None

    slot_row = await review_context_query.load_slot_row_for_review(conn, link.slot_id)
    if slot_row is None:
        return None

    analysis = await review_context_query.resolve_current_analysis(conn, slot_row)
    if not analysis.completed or analysis.job_uuid != str(job_uuid or "").strip():
        # 这次落库的运行不是"当前分析"（被更新的运行取代了，或不是当前版本）。
        # 绝不改写当前材料的复核进度。
        return None

    basis_token = review_session_store.build_analysis_basis_token(
        analysis.job_uuid, analysis.analysis_revision
    )
    completed_session = await review_session_store.get_latest_completed_session(
        conn, link.slot_id, basis_token=basis_token
    )
    review_state = REVIEW_COMPLETED if completed_session else REVIEW_REQUIRED

    result = await MaterialSlotService(conn).refresh_status(
        link.slot_id,
        analysis_state=ANALYSIS_DONE,
        review_state=review_state,
    )
    if not result:
        return None
    return {"slot_id": link.slot_id, **result}


async def best_effort_sync_slot_review_state(conn: Any, job_uuid: str) -> Optional[Dict[str, Any]]:
    """``sync_slot_review_state_for_job`` 的不抛异常包装。

    槽位/复核是旁路能力：它的任何故障都不允许让"分析结果落库"失败。
    与 ``safe_allocate_for_document`` 同一条纪律——但**不假装成功**：
    失败只记日志，返回 ``None``，绝不返回一个"已同步"的摘要。
    """
    try:
        return await sync_slot_review_state_for_job(conn, job_uuid)
    except Exception:  # noqa: BLE001 - 刻意兜住全部异常，见上
        logger.warning(
            "Review slot state sync failed for job %s", job_uuid, exc_info=True
        )
        return None


__all__ = [
    "sync_slot_review_state_for_job",
    "best_effort_sync_slot_review_state",
]
