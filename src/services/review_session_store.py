"""``review_sessions`` 的行级读写：复核生命周期的存储层。

分层理由
--------
``review_lifecycle_service`` 负责"什么时候该做什么"（门禁、失效判定、槽位状态接线），
本模块只负责"这些话怎么落库"。拆开是因为有**两个**互不相干的调用方需要写这张表：

1. 复核服务自己（start / complete / invalidate / reopen）；
2. 槽位服务的版本指针推进路径（新版本成为当前版本时，让旧复核失效）。

第 2 条如果直接反向导入 ``review_lifecycle_service``，就会形成
``material_slot_service ↔ review_lifecycle_service`` 的循环导入。把行级写入
放在这里，两边都只依赖它，依赖方向保持单向。

事务边界不由本模块负责
----------------------
本模块的每个函数只发一条语句，**锁的生命周期由调用方的事务决定**。
``review_transaction`` 是给复核写路径用的统一入口：

    async with review_transaction(conn):
        await lock_slot_row(conn, slot_id)      # 锁从这里开始持有
        ...
        await insert_session(conn, ...)          # 业务写入
    # 事务提交，锁才释放

不这么写的话（例如把 ``lock_slot_row`` 裸放在 autocommit 连接上），
``FOR UPDATE`` 会在**这一条语句**的事务结束时就释放，锁形同虚设——
WP3-A 的一轮独立评审用两个真库连接实证了这一点：没有显式事务时，
后一个连接对同一行的 ``UPDATE`` 完全不会被阻塞。

LOCK ORDER（全系统统一，不许有例外）
------------------------------------
身份 advisory 锁 → material_slots → fiscal_document_versions
    → review_sessions → review_obligation_decisions（WP3-B）→ analysis_jobs

本模块新增的两级必须挂在既有三级**之后**，理由：

- ``bind_document_version`` 已经是"槽位 → 版本"，随后才让旧复核失效，
  因此版本必须排在 review_sessions 之前；
- 重新分析钩子先让复核失效、再写分析代际，因此 review_sessions 必须排在
  analysis_jobs 之前；
- 复核服务本身**只**取"槽位 → 会话"，会话之后对 analysis_jobs 只有普通读，
  不参与成环。

反过来的顺序（先锁 analysis_jobs 再锁 review_sessions）会与重新分析钩子构成
ABBA 环；反过来先锁 review_sessions 再锁版本行，也会与 ``bind_document_version``
成环。因此这两级的先后不是风格问题。

失效是保留，不是删除
--------------------
本模块没有任何 ``DELETE FROM review_sessions``。旧会话必须留在表里供审计追溯
（V1 completed、V2 invalidated、V2 completed 三期都在），"开始新复核就删旧复核"
会让"这份材料历史上被谁复核过、当时结论是什么"永久消失。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from src.db.transaction import transaction_scope

logger = logging.getLogger(__name__)

#: 复核写路径的事务边界（与 WP1 共用同一套语义）。所有复核写路径都必须
#: 包在它里面，否则 ``FOR UPDATE`` 拿到的锁活不过单条语句。
#:
#: 语义细节见 ``src/db/transaction.py``：连接已在事务中时**直接复用外层事务**，
#: 不创建保存点；需要"内层失败外层继续"时必须显式写 ``conn.transaction()``。
review_transaction = transaction_scope

#: ``analysis_basis_token`` 的构造分隔符：``<job_uuid>:<analysis_revision>``。
#: job_uuid 是 UUID，不含冒号，因此用 ``rpartition`` 解析是安全的。
BASIS_TOKEN_SEPARATOR = ":"

_SESSION_COLUMNS = """
    id::text AS review_session_id,
    slot_id::text AS slot_id,
    document_version_id,
    analysis_job_uuid,
    analysis_basis_token,
    status,
    started_by,
    started_at,
    completed_by,
    completed_at,
    invalidated_at,
    invalidated_reason,
    review_result,
    created_at,
    updated_at
"""


def build_analysis_basis_token(job_uuid: str, analysis_revision: Any) -> str:
    """分析代际的对外令牌。代际变化必须在这里体现出来。

    ``analysis_revision`` 缺失（历史作业、字段还没落库）时用 ``0``：
    它仍然是一个**确定的**值，"没有代际信息"与"第 1 代"因此不会被混为一谈。
    """
    try:
        revision = int(analysis_revision)
    except (TypeError, ValueError):
        revision = 0
    return f"{str(job_uuid or '').strip()}{BASIS_TOKEN_SEPARATOR}{revision}"


def parse_analysis_basis_token(token: Any) -> Dict[str, Any]:
    """拆出 ``(job_uuid, analysis_revision)``，只用于展示与排障。

    解析不出来时 ``analysis_revision`` 为 ``None``（不是 0）：0 是合法代际，
    用它表示"解析失败"会让排障时看不出区别。
    """
    text = str(token or "")
    job_uuid, separator, raw_revision = text.rpartition(BASIS_TOKEN_SEPARATOR)
    if not separator:
        return {"job_uuid": text, "analysis_revision": None}
    try:
        revision: Optional[int] = int(raw_revision)
    except (TypeError, ValueError):
        revision = None
    return {"job_uuid": job_uuid, "analysis_revision": revision}


def _json_dict(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except Exception:  # noqa: BLE001 - 解析失败按空对象处理
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def to_jsonb(value: Any) -> str:
    """JSONB 参数序列化。

    本仓库没有给 asyncpg 注册 JSON codec，写 JSONB 必须显式 ``$n::jsonb`` +
    字符串（与 ``analysis_result_store._to_json`` 同一手法）。
    """
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def session_row_to_dict(row: Any) -> Optional[Dict[str, Any]]:
    """把会话行转成对外字典（时间字段保持 datetime，序列化交给契约层）。"""
    if row is None:
        return None
    payload = dict(row)
    payload["review_result"] = _json_dict(payload.get("review_result"))
    return payload


async def lock_slot_row(conn: Any, slot_id: Any) -> Optional[Dict[str, Any]]:
    """按全局锁序取槽位行锁——**任何**复核写路径的第一步。

    复核的三个写动作（start/complete/reopen）都必须先拿这把锁：它同时把
    "版本指针推进"与"重新分析"两条并发路径串行化到同一把锁上，
    否则会出现"复核完成与当前版本切换同时成功"这种自相矛盾的终态。
    锁不到行返回 ``None``（槽位不存在），由调用方决定 404 还是别的语义。
    """
    row = await conn.fetchrow(
        """
        SELECT id, current_document_version_id
        FROM material_slots
        WHERE id = $1
        FOR UPDATE
        """,
        slot_id,
    )
    return dict(row) if row is not None else None


async def get_active_session(conn: Any, slot_id: Any, *, for_update: bool = False) -> Optional[Dict]:
    """该槽位当前进行中的会话（至多一条，由 partial unique index 保证）。

    ``for_update=True`` 时对会话行加锁。完成/重开这类"必须看到别人的提交结果"
    的路径必须加锁：不加锁时两个并发 complete 会各自读到 ``in_progress``，
    各自写下完成记录与审计事件——用户看到两条完成痕迹、审计里出现两次业务副作用。
    """
    suffix = " FOR UPDATE" if for_update else ""
    row = await conn.fetchrow(
        f"""
        SELECT {_SESSION_COLUMNS}
        FROM review_sessions
        WHERE slot_id = $1 AND status = 'in_progress'
        ORDER BY created_at DESC, id DESC
        LIMIT 1{suffix}
        """,
        slot_id,
    )
    return session_row_to_dict(row)


async def get_session_by_id(
    conn: Any, review_session_id: Any, *, for_update: bool = False
) -> Optional[Dict]:
    """按 id 取会话，可选加锁（用于"锁定会话后再判定"的路径）。"""
    suffix = " FOR UPDATE" if for_update else ""
    row = await conn.fetchrow(
        f"""
        SELECT {_SESSION_COLUMNS}
        FROM review_sessions
        WHERE id = $1{suffix}
        """,
        review_session_id,
    )
    return session_row_to_dict(row)


async def get_latest_completed_session(
    conn: Any, slot_id: Any, *, basis_token: Optional[str] = None
) -> Optional[Dict]:
    """该槽位最近一次**已完成**的会话。

    ``basis_token`` 非空时只找同一分析代际上的完成记录：复核结论是绑在
    "某一代分析结果"上的，跨代际沿用等于拿上一代结论冒充这一代。
    """
    if basis_token is None:
        row = await conn.fetchrow(
            f"""
            SELECT {_SESSION_COLUMNS}
            FROM review_sessions
            WHERE slot_id = $1 AND status = 'completed'
            ORDER BY completed_at DESC NULLS LAST, created_at DESC, id DESC
            LIMIT 1
            """,
            slot_id,
        )
    else:
        row = await conn.fetchrow(
            f"""
            SELECT {_SESSION_COLUMNS}
            FROM review_sessions
            WHERE slot_id = $1 AND status = 'completed' AND analysis_basis_token = $2
            ORDER BY completed_at DESC NULLS LAST, created_at DESC, id DESC
            LIMIT 1
            """,
            slot_id,
            basis_token,
        )
    return session_row_to_dict(row)


async def list_history(conn: Any, slot_id: Any, *, limit: int = 20) -> List[Dict[str, Any]]:
    """已终结的会话历史（completed / invalidated），最新在前。

    历史列表**不排除**与当前会话同代际的完成记录：重开复核后，那次完成
    仍然是发生过的事实，必须留在历史里。
    """
    rows = await conn.fetch(
        f"""
        SELECT {_SESSION_COLUMNS}
        FROM review_sessions
        WHERE slot_id = $1 AND status <> 'in_progress'
        ORDER BY COALESCE(completed_at, invalidated_at) DESC NULLS LAST, created_at DESC, id DESC
        LIMIT $2
        """,
        slot_id,
        max(0, int(limit)),
    )
    return [item for item in (session_row_to_dict(row) for row in rows) if item is not None]


async def insert_session(
    conn: Any,
    *,
    slot_id: Any,
    document_version_id: Any,
    analysis_job_uuid: str,
    analysis_basis_token: str,
    started_by: str,
) -> Dict[str, Any]:
    """新开一条 ``in_progress`` 会话。

    唯一约束 ``uq_review_sessions_active`` 是这里的第二道门：调用方已经先查过
    "有没有活动会话"，但那只是为了让正常路径不报错；两个浏览器同时点进来的
    情形由数据库拦下（调用方必须捕获唯一冲突并回读既有会话，见服务层）。
    """
    row = await conn.fetchrow(
        f"""
        INSERT INTO review_sessions (
            slot_id, document_version_id, analysis_job_uuid,
            analysis_basis_token, status, started_by
        )
        VALUES ($1, $2, $3, $4, 'in_progress', $5)
        RETURNING {_SESSION_COLUMNS}
        """,
        slot_id,
        document_version_id,
        analysis_job_uuid,
        analysis_basis_token,
        started_by,
    )
    return session_row_to_dict(row) or {}


async def complete_session(
    conn: Any,
    *,
    review_session_id: Any,
    completed_by: str,
    review_result: Any,
) -> Optional[Dict[str, Any]]:
    """把会话标记为完成，写入完成人与复核快照。

    ``WHERE status = 'in_progress'`` 是幂等与并发的关键：重复 complete 不会
    覆盖第一次的完成人/完成时间（第二次 UPDATE 命中 0 行，调用方回读既有记录）。
    覆盖会让"谁复核的"变成"最后一个点按钮的人"，而审计要的是第一个。
    """
    row = await conn.fetchrow(
        f"""
        UPDATE review_sessions
        SET status = 'completed',
            completed_by = $2,
            completed_at = NOW(),
            review_result = $3::jsonb,
            updated_at = NOW()
        WHERE id = $1 AND status = 'in_progress'
        RETURNING {_SESSION_COLUMNS}
        """,
        review_session_id,
        completed_by,
        to_jsonb(review_result),
    )
    return session_row_to_dict(row)


async def invalidate_session(
    conn: Any, *, review_session_id: Any, reason: str
) -> Optional[Dict[str, Any]]:
    """把单条会话置为失效。

    ``status IN ('in_progress', 'completed')`` 而不是只处理 in_progress：
    **显式重开复核**要让的就是一条已完成的会话（用户点"重新复核"）。
    只匹配 in_progress 时那一步会静默失效（UPDATE 命中 0 行），
    结果是"旧完成记录还挂着、新会话已经开了"——同一份材料同时存在两条
    看起来都有效的复核结论。已经 invalidated 的会话不会被再次改写（幂等）。
    """
    row = await conn.fetchrow(
        f"""
        UPDATE review_sessions
        SET status = 'invalidated',
            invalidated_at = NOW(),
            invalidated_reason = $2,
            updated_at = NOW()
        WHERE id = $1 AND status IN ('in_progress', 'completed')
        RETURNING {_SESSION_COLUMNS}
        """,
        review_session_id,
        reason,
    )
    return session_row_to_dict(row)


async def invalidate_sessions_for_job(
    conn: Any, analysis_job_uuid: str, *, reason: str
) -> List[Dict[str, Any]]:
    """让某个 job 上的活动/已完成会话全部失效（重新分析钩子用）。

    按 ``analysis_job_uuid`` 而不是按槽位：一个槽位的会话在正常情况下都指向
    同一个 job，但"精确到受影响的 job"能避免将来多 job 场景下误伤无关会话。

    ``IN ('in_progress', 'completed')`` 而不是只处理 ``in_progress``：
    已完成复核挂的是**旧那一代**分析结果，重新分析后它不再有效，
    必须一起失效——否则页面上会同时出现"已完成复核"和"分析结果已更新"。
    """
    rows = await conn.fetch(
        """
        UPDATE review_sessions
        SET status = 'invalidated',
            invalidated_at = NOW(),
            invalidated_reason = $2,
            updated_at = NOW()
        WHERE analysis_job_uuid = $1 AND status IN ('in_progress', 'completed')
        RETURNING id::text AS review_session_id, analysis_basis_token
        """,
        str(analysis_job_uuid or "").strip(),
        reason,
    )
    # 返回被失效会话的**身份**（而不只是条数）：调用方需要在提交之后按
    # (session, basis) 精确清除对应的文件锁——无条件按 job 清会误删
    # 并发下后来会话留下的锁。
    return [dict(row) for row in rows]


async def invalidate_stale_version_sessions(conn: Any, slot_id: Any, *, reason: str) -> int:
    """让"钉住的版本已不是槽位当前版本"的会话失效（版本指针推进后调用）。

    **这条语句是自守卫的**：它自己比较 ``r.document_version_id`` 与
    ``s.current_document_version_id``，只有在两者不一致时才失效。调用方因此
    可以无条件、幂等地执行它——不需要先知道"指针这次到底动没动"。

    这个设计是有意的。绑定路径（``bind_document_version``）里"指针是否推进"
    由一条 SQL 的 WHERE 条件决定，把那个结论回传给 Python 再决定要不要失效，
    等于把同一条判断写两遍；两处一旦漂移，就会出现"指针动了但复核没失效"
    这种最难发现的静默不一致。

    为什么必须有它：复核完成与版本推进是两个并发事务。若只靠"下次打开页面
    才发现"，就存在"复核已完成、而它复核的是已经被替换掉的那一版文件"的窗口，
    且这个窗口的产物看起来完全正常。挂钩在版本推进事务里，两者被同一把
    槽位行锁串行化，终态因此只有两种：会话失效，或复核看到版本已变而拒绝完成。
    """
    result = await conn.execute(
        """
        UPDATE review_sessions AS r
        SET status = 'invalidated',
            invalidated_at = NOW(),
            invalidated_reason = $2,
            updated_at = NOW()
        FROM material_slots AS s
        WHERE s.id = $1
          AND r.slot_id = s.id
          AND r.status IN ('in_progress', 'completed')
          AND r.document_version_id IS DISTINCT FROM s.current_document_version_id
        """,
        slot_id,
        reason,
    )
    return _affected_rows(result)


async def clear_analysis_result_fingerprint(conn: Any, job_uuid: str) -> int:
    """清空分析结果指纹，让**下一次落库的结果**必定递增分析代际。

    为什么"清空"而不是"在这里直接 +1"：代际的定义是"已经落库过结果的分析代际数"
    （见 migration 0020 的注释）。重新分析只是把旧结果作废，新结果还没产生；
    在这里 +1 会让一次重新分析涨两代，而"代际"这个数字就不再能从库里读出来含义。

    清空之后，下一次落库必然走"指纹为空 → 递增"的分支，因此**内容与上一代
    逐字相同**的重新分析同样会改变 ``analysis_basis_token``。这一点是本轮
    硬要求：人复核的是"这一次的分析"，不是"看起来一样的字"。
    """
    result = await conn.execute(
        """
        UPDATE analysis_jobs
        SET analysis_result_fingerprint = NULL, updated_at = NOW()
        WHERE job_uuid = $1
        """,
        str(job_uuid or "").strip(),
    )
    return _affected_rows(result)


def _affected_rows(result: Any) -> int:
    """把 asyncpg 的 ``"UPDATE n"`` 状态串转成整数；解析不出来时返回 0。

    解析失败按 0 处理而不是抛错：调用方用这个数字做审计/日志，
    让它因为一个格式差异炸掉整条复核链路是本末倒置。
    """
    text = str(result or "")
    parts = text.split()
    if len(parts) < 2:
        return 0
    try:
        return int(parts[-1])
    except (TypeError, ValueError):
        return 0


__all__ = [
    "BASIS_TOKEN_SEPARATOR",
    "review_transaction",
    "build_analysis_basis_token",
    "parse_analysis_basis_token",
    "to_jsonb",
    "session_row_to_dict",
    "lock_slot_row",
    "get_active_session",
    "get_session_by_id",
    "get_latest_completed_session",
    "list_history",
    "insert_session",
    "complete_session",
    "invalidate_session",
    "invalidate_sessions_for_job",
    "invalidate_stale_version_sessions",
    "clear_analysis_result_fingerprint",
]
