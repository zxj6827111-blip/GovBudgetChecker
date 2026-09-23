"""``review_obligation_decisions`` 的行级读写：人工补核决定的存储层（WP3-B）。

分层理由与 ``review_session_store`` 相同：服务层（``review_lifecycle_service``）
负责"什么时候该做什么"，本模块只负责"这些话怎么落库"。

并发语义（任务书 §十五：唯一约束与乐观锁**都**要有）
---------------------------------------------------

1. **首次表态**：``INSERT ... ON CONFLICT DO NOTHING``。两个复核人对同一会话的
   同一义务同时首次写入时，后到者命中 ``uq_review_obligation_decisions_scope``
   唯一约束而拿到 ``None``——服务层据此回 409，**不允许**用 ``DO UPDATE``
   把先到者的结论悄悄冲掉。
2. **改写已有决定**：``UPDATE ... WHERE id = $n AND revision = $n``（乐观锁）。
   调用方必须携带它读到的 ``revision``；命中 0 行说明别人已经改过了
   （或行不存在），同样回 409。
3. **槽位行锁**是第三道秩序：所有写决定的服务层路径都先拿
   ``review_session_store.lock_slot_row``，把同槽位的补核写入与完成复核
   串行化。锁序遵守全局 LOCK ORDER（见 ``review_session_store`` 模块顶部）：
   本表的行级访问只发生在 ``material_slots → review_sessions`` 之后。

失效语义（任务书 §十/§十六）
---------------------------

决定绑定 ``review_session_id``，**失效是保留，不是删除**——本模块没有任何
``DELETE``。复核会话失效后，它的决定留在表里供审计追溯，但完成门禁只读
"当前有效会话"的决定，旧结论不会漏进新复核。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

_DECISION_COLUMNS = """
    id::text AS decision_record_id,
    review_session_id::text AS review_session_id,
    slot_id::text AS slot_id,
    obligation_id,
    decision,
    note,
    evidence_reference,
    reviewer,
    reviewed_at,
    revision,
    created_at,
    updated_at
"""


def decision_row_to_dict(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return dict(row)


async def list_session_decisions(
    conn: Any, review_session_id: Any
) -> List[Dict[str, Any]]:
    """某条复核会话上的全部人工补核决定（门禁与详情共用的唯一读取入口）。

    只按会话读：跨会话拼接"每条义务最新的决定"会把**上一次复核**的结论
    混进这一次——那正是任务书禁止的 ``obligation_id + job_uuid`` 式关联。
    """
    rows = await conn.fetch(
        f"""
        SELECT {_DECISION_COLUMNS}
        FROM review_obligation_decisions
        WHERE review_session_id = $1
        ORDER BY created_at, id
        """,
        review_session_id,
    )
    return [item for item in (decision_row_to_dict(row) for row in rows) if item is not None]


async def insert_decision(
    conn: Any,
    *,
    review_session_id: Any,
    slot_id: Any,
    obligation_id: str,
    decision: str,
    note: Optional[str],
    evidence_reference: Optional[str],
    reviewer: str,
) -> Optional[Dict[str, Any]]:
    """首次写入一条决定；并发撞唯一约束时返回 ``None``（调用方回 409）。

    ``ON CONFLICT DO NOTHING`` 而不是 ``DO UPDATE``：后者会在后到者的视角里
    **静默覆盖**先到者的决定——两个复核人同时表态时，必须先有人看见冲突，
    再带着新的 ``revision`` 显式改写。
    """
    row = await conn.fetchrow(
        f"""
        INSERT INTO review_obligation_decisions (
            review_session_id, slot_id, obligation_id,
            decision, note, evidence_reference, reviewer
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (review_session_id, obligation_id) DO NOTHING
        RETURNING {_DECISION_COLUMNS}
        """,
        review_session_id,
        slot_id,
        obligation_id,
        decision,
        note,
        evidence_reference,
        reviewer,
    )
    return decision_row_to_dict(row)


async def update_decision_if_revision_matches(
    conn: Any,
    *,
    decision_record_id: Any,
    expected_revision: Any,
    decision: str,
    note: Optional[str],
    evidence_reference: Optional[str],
    reviewer: str,
) -> Optional[Dict[str, Any]]:
    """乐观锁改写：``revision`` 匹配才更新并 +1，命中 0 行返回 ``None``。

    返回 ``None`` 的唯一业务语义是"有人先改了（或行已不存在）"：
    调用方必须回 409，而不是把这次写入降级成"算了不改了"。
    ``reviewer`` 记录**最后一次**改写人——完整改写轨迹由审计日志承担
    （每次写入都会落一条 ``obligation.reviewed`` / ``obligation.updated``）。
    """
    row = await conn.fetchrow(
        f"""
        UPDATE review_obligation_decisions
        SET decision = $3,
            note = $4,
            evidence_reference = $5,
            reviewer = $6,
            reviewed_at = NOW(),
            revision = revision + 1,
            updated_at = NOW()
        WHERE id = $1 AND revision = $2
        RETURNING {_DECISION_COLUMNS}
        """,
        decision_record_id,
        expected_revision,
        decision,
        note,
        evidence_reference,
        reviewer,
    )
    return decision_row_to_dict(row)


__all__ = [
    "decision_row_to_dict",
    "list_session_decisions",
    "insert_decision",
    "update_decision_if_revision_matches",
]
