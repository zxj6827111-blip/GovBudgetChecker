"""槽位的持久化：把归属判定结果落库，并维护文件版本指针与状态缓存。

分工
----
- ``material_slot_resolver`` 决定"这份材料属于哪条槽位"（纯函数，
  线上与回填 dry-run 共用）；
- 本模块只负责"把这个结论写进数据库"，不做任何归属判断。

三条不可让步的写入纪律
----------------------
1. **跨槽重绑 fail-closed。** 一个文件版本已经属于槽位 A 时，普通绑定动作
   不许把它改挂到槽位 B。静默移动会留下"槽位 A 的当前版本指针指向 B 的版本"
   这种双向不一致，而双向不一致正是材料串线的来源。真正的改判/合并是显式的
   人工业务动作，不属于普通 bind。
2. **一次分配是一个原子单元。** 槽位 upsert、版本绑定、当前版本指针推进、
   状态刷新写在同一个事务里：要么全成，要么全不成。部分写入比整体失败更糟——
   失败看得见，部分写入看不见。
3. **状态只有一个生产者。** 所有状态都出自 ``material_slot_status`` 的状态机，
   本模块任何写路径都不直接拼状态值。

LOCK ORDER（全系统统一，不许有例外）
------------------------------------
    身份 advisory 锁  →  material_slots 行  →  fiscal_document_versions 行

三级全序，本模块五个写方法一律遵守：

| 方法 | 取锁顺序 |
| --- | --- |
| ``upsert_from_decision`` | advisory(slot_key) → 槽位（INSERT DO NOTHING → SELECT FOR UPDATE） |
| ``refresh_status`` | 槽位（SELECT FOR UPDATE） |
| ``mark_not_applicable`` | 槽位（UPDATE → refresh_status 复用同一事务） |
| ``bind_document_version`` | 槽位（SELECT FOR UPDATE）→ 版本（SELECT FOR UPDATE） |
| ``allocate_for_document`` | advisory+槽位（upsert）→ 版本（bind）→ 槽位（refresh，同一事务内复用） |

**为什么槽位与版本之间必须统一顺序**：``allocate_for_document`` 天然是
"先槽位后版本"，而 ``bind_document_version`` 最初写成"先版本后槽位"。
两者并发指向**同一个 (槽位, 版本) 对**时会形成经典的 ABBA 死锁——
一个持有槽位等版本，另一个持有版本等槽位。把 ``bind_document_version``
改成先锁槽位就消除了这个环，代价只是多一条 SELECT。

**为什么最外层还需要身份 advisory 锁**：``material_slots`` 有两个唯一约束
（``slot_key`` 与自然键表达式索引 ``uq_material_slots_identity``）。
两个事务并发插入同一身份时，每个唯一索引各有一个"推测插入"标记，
双方可能分别等待对方在**不同索引**上的标记，形成环路——
真库实测确实报 ``DeadlockDetectedError``。advisory 锁把同一身份的首次创建
串行化，推测插入竞争不复存在。

ABBA 不可能成立的理由：advisory 锁永远是本模块取的**第一把**锁，
行锁顺序是固定的"槽位 → 版本"，两条规则合起来构成全序，
不存在"某条路径反向持有"的情况。

``_advance_current_version`` 里的 ``UPDATE ... FROM fiscal_document_versions``
只锁目标表（槽位）的行，``FROM`` 与 ``EXISTS`` 子查询都是普通 MVCC 读，
不会取版本行锁，因此不破坏上述顺序。

永不阻断主流程
--------------
``safe_allocate_for_document`` 吞掉全部异常并返回带原因的摘要。
材料台账是旁路能力，它的故障不允许让 PDF 解析/入库失败。
但"不抛异常"不等于"假装成功"：冲突与失败必须出现在返回值里，
不允许在发生冲突时返回 ``bound=true``。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.schemas.material_slot import (
    SlotIdentity,
    slot_identity_is_resolved,
)
from src.services import material_slot_resolver as resolver
from src.services.material_slot_status import (
    ANALYSIS_NOT_STARTED,
    REVIEW_NONE,
    derive_slot_status,
    infer_progress_state,
)

logger = logging.getLogger(__name__)

#: 绑定冲突的原因码。必须能被调用方与运维直接看到，
#: 不允许退化成一句笼统的"分配失败"。
BINDING_REASON_CONFLICT = "slot_binding_conflict"
BINDING_REASON_VERSION_MISSING = "slot_version_missing"

#: 已确认口径才能参与冲突判定。``unknown`` 表示"这次没识别出来"，
#: 它不是一种口径，因此不与任何值冲突。
_CONFIRMED_CALIBERS = ("summary", "self")


class SlotBindingError(RuntimeError):
    """文件版本绑定被拒绝。"""

    reason = "slot_binding_error"

    def __init__(self, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.detail: Dict[str, Any] = detail


class SlotBindingConflict(SlotBindingError):
    """该文件版本已属于另一个槽位，普通绑定不允许跨槽移动。"""

    reason = BINDING_REASON_CONFLICT

    def __init__(self, document_version_id: Any, existing_slot_id: Any, target_slot_id: Any) -> None:
        super().__init__(
            f"document_version {document_version_id} 已绑定槽位 {existing_slot_id}，"
            f"拒绝改挂到 {target_slot_id}",
            document_version_id=str(document_version_id),
            existing_slot_id=str(existing_slot_id),
            target_slot_id=str(target_slot_id),
        )


class SlotBindingTargetMissing(SlotBindingError):
    """要绑定的文件版本不存在。"""

    reason = BINDING_REASON_VERSION_MISSING

    def __init__(self, document_version_id: Any) -> None:
        super().__init__(
            f"document_version {document_version_id} 不存在",
            document_version_id=str(document_version_id),
        )


class SlotIdentityRowMissing(RuntimeError):
    """刚确保存在的槽位行紧接着又读不到。

    这不是预期路径，出现即说明有并发删除，或身份行的写入没按预期落地。
    抛错让事务整体回滚，比返回 ``None`` 安全：``None`` 会被调用方理解成
    "本次没建槽位"，而库里可能已经留下半成品。
    """

    reason = "slot_identity_row_missing"

    def __init__(self, slot_key: str) -> None:
        super().__init__(f"槽位 {slot_key} 在确保存在后读取不到")
        self.slot_key = slot_key


def in_transaction(conn: Any) -> bool:
    """连接是否已处于事务中。

    取不到状态时按"未开启"处理：多开一层事务（asyncpg 会退化为 SAVEPOINT）
    不会破坏正确性，而"以为在事务里、其实不在"会让行锁在语句结束就释放，
    那才是真正的风险。因此不确定时选择更安全的一侧。
    """
    checker = getattr(conn, "is_in_transaction", None)
    if not callable(checker):
        return False
    try:
        return bool(checker())
    except Exception:  # pragma: no cover - 驱动实现差异的兜底
        return False


@asynccontextmanager
async def _transaction(conn: Any):
    """需要时开启事务；已在事务中则直接复用，不重复包裹。

    重复包裹在 asyncpg 下会退化成 SAVEPOINT——语义仍然正确，但每层都多一次
    往返。调用链已经很短，这里显式区分，读代码时不必去猜嵌套了几层。
    """
    if in_transaction(conn):
        yield
        return
    async with conn.transaction():
        yield


class MaterialSlotService:
    """槽位读写。构造时传入一个 asyncpg 连接。"""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    # ---- 写入 ---------------------------------------------------------------

    async def upsert_from_decision(
        self,
        decision: resolver.SlotAllocationDecision,
        *,
        caliber: str = "unknown",
        due_at: Optional[datetime] = None,
        expected_source_url: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """按判定结论建槽或复用既有槽位，返回槽位行。

        已确认的口径不会被静默改写：新一次识别与前一次冲突时，保留已确认值，
        并把矛盾观测记进 ``caliber_conflict_candidate`` 等人工裁决。
        这一步是必须的——口径决定"两笔数字能不能相加"，静默取最新一次识别
        等于让口径随分析次数漂移。

        写法是"先确保存在 → 再锁 → 锁内读最新事实 → 锁内判定 → 写回"，分三步。
        为什么不沿用"SELECT ... FOR UPDATE 再 ON CONFLICT DO UPDATE"：
        ``SELECT ... FOR UPDATE`` **锁不住一条不存在的行**。首次创建同一个槽位时，
        两个事务都会读到空行、各自在锁外算好口径，随后一个 INSERT、一个走
        ON CONFLICT 分支把锁外算出的结果盖上去——结果就是 ``summary`` 与 ``self``
        两者只留下一个、冲突证据被抹掉。这正是本轮要修掉的竞态。

        三步各自的职责被刻意分开，避免职责重叠再次制造竞态：

        1. 第一步只保证"这一行存在"，不承担覆盖业务事实的职责；
        2. 第二步拿到**真实存在**的行锁，两个事务在此被数据库串行化；
        3. 第三步的判定与写入都发生在持锁期间，读到的就是最新已提交值。
        """
        if decision.identity is None:
            return None

        identity: SlotIdentity = decision.identity
        async with _transaction(self._conn):
            await self._lock_slot_identity(identity.slot_key)
            await self._ensure_slot_row(
                identity=identity,
                decision=decision,
                caliber=caliber,
                due_at=due_at,
                expected_source_url=expected_source_url,
            )

            # 到这里行一定存在，FOR UPDATE 才真正取到锁。
            current = await self._conn.fetchrow(
                """
                SELECT id, caliber, caliber_conflict_candidate,
                       current_document_version_id, applicability_status, due_at
                FROM material_slots
                WHERE slot_key = $1
                FOR UPDATE
                """,
                identity.slot_key,
            )
            if current is None:
                # 刚确保存在、紧接着又读不到，说明有并发删除或其他不变量被破坏。
                # 抛错让事务整体回滚，不返回 None——静默返回 None 会让调用方以为
                # "没建槽位"，而库里其实可能留下半成品。
                raise SlotIdentityRowMissing(identity.slot_key)

            resolved_caliber, conflict_candidate = _resolve_caliber(
                current["caliber"],
                current["caliber_conflict_candidate"],
                caliber,
            )
            has_document = current["current_document_version_id"] is not None
            # 状态推导要用**库里已有的适用性与截止时间**：人工确认过"本年度无此材料"
            # 的槽位，不能被一次自动重放重新推回 applicable。
            status_result = derive_slot_status(
                applicability_status=str(current["applicability_status"] or "applicable"),
                due_at=due_at if current["due_at"] is None else current["due_at"],
                identity_resolved=identity.is_resolved,
                caliber_conflict=bool(conflict_candidate),
                has_current_document=bool(has_document),
                # 刻意回到"已上传待分析"：本方法只在一次**新的分析运行**之后被调用，
                # 而此前的人工复核结论针对的是上一次分析结果。沿用旧结论等于拿
                # 过期结论冒充已复核（PLAN 的 WP-RVW-01 也要求重分析使复核失效）。
                # 方向是 fail-closed：宁可要求人工再看一遍，也不宣称已完成。
                analysis_state=ANALYSIS_NOT_STARTED,
                review_state=REVIEW_NONE,
            )

            row = await self._conn.fetchrow(
                """
                UPDATE material_slots
                SET jurisdiction_org_id = COALESCE($2, jurisdiction_org_id),
                    jurisdiction_name = COALESCE($3, jurisdiction_name),
                    department_org_id = COALESCE($4, department_org_id),
                    department_name = COALESCE($5, department_name),
                    subject_org_name = COALESCE(NULLIF($6, ''), subject_org_name),
                    subject_org_code = COALESCE($7, subject_org_code),
                    caliber = $8,
                    caliber_conflict_candidate = $9,
                    expected_source_url = COALESCE($10, expected_source_url),
                    due_at = COALESCE(due_at, $11),
                    status = $12,
                    status_reason = $13,
                    updated_at = NOW()
                WHERE id = $1
                RETURNING id, slot_key, status, status_reason
                """,
                current["id"],
                decision.jurisdiction_org_id,
                decision.jurisdiction_name,
                decision.department_org_id,
                decision.department_name,
                decision.subject_org_name or identity.subject_org_id,
                decision.subject_org_code,
                resolved_caliber,
                conflict_candidate or None,
                expected_source_url,
                due_at,
                status_result.status,
                status_result.reason,
            )
        return dict(row) if row is not None else None

    async def _lock_slot_identity(self, slot_key: str) -> None:
        """按身份串行化槽位的首次创建。

        **为什么光靠 ``INSERT ... ON CONFLICT DO NOTHING`` 不够**（实测结论）：
        ``material_slots`` 上有两个唯一约束——``slot_key`` 列约束，以及自然键
        表达式索引 ``uq_material_slots_identity``。两个事务同时插入同一个身份时，
        PostgreSQL 会为每个唯一索引各建一个"推测插入（speculative insertion）"
        标记，两个事务可能分别等待对方在**不同索引**上的标记，形成环路。
        真库实测结果就是 ``DeadlockDetectedError``（等待推测记号上的 ShareLock）。

        事务级 advisory 锁把同一身份的首次创建彻底串行化，推测插入竞争不复存在。
        锁在事务结束时自动释放，不需要手工解锁。

        锁键用 ``hashtextextended(slot_key, 0)`` 取 64 位哈希。不同身份撞哈希时
        只会互相多等一会儿（性能），不会让两条身份互相覆盖（正确性）。

        锁层级：这是**最外层**的一把锁，永远在行锁之前取，见模块顶部 LOCK ORDER。
        """
        await self._conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
            slot_key,
        )

    async def _ensure_slot_row(
        self,
        *,
        identity: SlotIdentity,
        decision: resolver.SlotAllocationDecision,
        caliber: str,
        due_at: Optional[datetime],
        expected_source_url: Optional[str],
    ) -> None:
        """第一步：确保身份行存在。**只负责存在性，不覆盖任何已有业务事实。**

        ``ON CONFLICT (slot_key) DO NOTHING`` 是关键：已存在的行一个字段都不动，
        因此这一步无论被多少并发事务同时执行都只会留下一条记录，
        也不可能拿锁外算出的旧结论覆盖别人刚写下的口径。

        并发下的行为：两个事务同时首次创建时，一个真正插入，另一个在此等待
        对方提交后跳过。随后双方在第二步的行锁上串行，各自读到同一份最新值。
        """
        await self._conn.execute(
            """
            INSERT INTO material_slots (
                slot_key,
                jurisdiction_org_id, jurisdiction_name,
                department_org_id, department_name,
                subject_org_id, subject_org_name, subject_org_code,
                subject_kind, subject_level, material_scope,
                caliber, fiscal_year, report_kind,
                due_at, expected_source_url, mapping_key
            )
            VALUES (
                $1,
                $2, $3,
                $4, $5,
                $6, $7, $8,
                $9, $10, $11,
                $12, $13, $14,
                $15, $16, $17
            )
            ON CONFLICT (slot_key) DO NOTHING
            """,
            identity.slot_key,
            decision.jurisdiction_org_id,
            decision.jurisdiction_name,
            decision.department_org_id,
            decision.department_name,
            identity.subject_org_id,
            decision.subject_org_name or identity.subject_org_id,
            decision.subject_org_code,
            identity.subject_kind,
            identity.subject_kind,
            identity.material_scope,
            _normalize_caliber(caliber),
            identity.fiscal_year,
            identity.report_kind,
            due_at,
            expected_source_url,
            identity.mapping_key,
        )

    async def bind_document_version(
        self, slot_id: Any, document_version_id: Any
    ) -> Dict[str, Any]:
        """把文件版本挂到槽位，并把"当前版本"指针推进到较新者。

        允许的两种情况：版本尚未归属任何槽位，或已归属**同一个**槽位
        （幂等重放）。其它情况一律拒绝并抛 ``SlotBindingConflict``。

        整段在一个事务里，并对版本行 ``FOR UPDATE``：没有行锁的话，
        两个并发请求可以同时读到 ``slot_id IS NULL`` 然后各自绑定，
        结果是后写的覆盖先写的、而两边都以为自己成功。

        **锁顺序：先槽位行，后版本行。** 这是全系统统一顺序（见模块顶部的
        ``LOCK ORDER``）。本方法此前先锁版本、再在推进指针时锁槽位，
        与 ``allocate_for_document`` 的"先锁槽位再绑版本"正好相反，
        两者并发指向同一个 (槽位, 版本) 对时会形成 ABBA 死锁。
        先锁槽位把顺序统一过来，代价只是多一条 SELECT。
        """
        async with _transaction(self._conn):
            # 先按全局锁序取槽位行锁。槽位不存在时这里锁不到任何行，
            # 之后的 UPDATE 会由外键约束拦下，不需要在这里额外判空。
            await self._conn.fetchrow(
                """
                SELECT id
                FROM material_slots
                WHERE id = $1
                FOR UPDATE
                """,
                slot_id,
            )
            row = await self._conn.fetchrow(
                """
                SELECT id, slot_id
                FROM fiscal_document_versions
                WHERE id = $1
                FOR UPDATE
                """,
                document_version_id,
            )
            if row is None:
                raise SlotBindingTargetMissing(document_version_id)

            existing_slot_id = row["slot_id"]
            if existing_slot_id is not None and str(existing_slot_id) != str(slot_id):
                raise SlotBindingConflict(document_version_id, existing_slot_id, slot_id)

            if existing_slot_id is None:
                await self._conn.execute(
                    """
                    UPDATE fiscal_document_versions
                    SET slot_id = $2
                    WHERE id = $1 AND slot_id IS NULL
                    """,
                    document_version_id,
                    slot_id,
                )
            await self._advance_current_version(slot_id, document_version_id)
        return await self.get_slot_by_id(slot_id) or {}

    async def _advance_current_version(self, slot_id: Any, document_version_id: Any) -> None:
        """把当前版本指针推进到较新的版本。

        排序键是 ``(created_at, id)`` 而不是单独 ``created_at``：
        批量回填时同一秒内建出的版本很常见，只比时间戳会让指针在等值版本之间
        随机停靠，同一个槽位重复回填可能得到不同的"当前版本"。
        id 是单调递增的序列，同时间戳时它给出的顺序是稳定的。
        """
        await self._conn.execute(
            """
            UPDATE material_slots AS s
            SET current_document_version_id = v.id,
                updated_at = NOW()
            FROM fiscal_document_versions AS v
            WHERE s.id = $1
              AND v.id = $2
              AND (
                    s.current_document_version_id IS NULL
                    OR EXISTS (
                        SELECT 1 FROM fiscal_document_versions AS cur
                        WHERE cur.id = s.current_document_version_id
                          AND (cur.created_at, cur.id) <= (v.created_at, v.id)
                    )
                  )
            """,
            slot_id,
            document_version_id,
        )

    async def refresh_status(
        self,
        slot_id: Any,
        *,
        analysis_state: Optional[str] = None,
        review_state: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> Dict[str, str]:
        """按库里的事实重算并落库槽位状态。

        身份完整性用的是全系统统一判定 ``slot_identity_is_resolved``，
        而不是"看 mapping_key 是否为空"这类简化——后者会把"年份没认出来"
        当成身份已确认，让未确认的材料一路滑进 ``not_due``。

        未显式传入 ``analysis_state`` / ``review_state`` 时，从当前状态反推
        （``infer_progress_state``），而不是默认成"分析未开始"：调用方往往只
        关心槽位事实（版本、口径），一次无关刷新不该把"待人工复核"打回"已上传"。

        **本方法自带事务与行锁，不依赖调用方替它兜底。** 此前它"读事实 → 推导 →
        写状态"三步之间没有任何保护：读完之后若有别的事务绑定了文件版本，
        它仍会按读到的旧快照把状态写成"未上传"，于是库里出现
        ``current_document_version_id`` 有值而 ``status`` 还是 ``missing``
        这种缓存与事实互相矛盾的行。当前调用方（``allocate_for_document``、
        ``mark_not_applicable``）恰好已经开了事务，但那是调用约定而不是保证——
        WP2/WP3 会直接调它，靠约定维持的正确性迟早会破。

        锁顺序：只锁槽位行，与全系统统一顺序一致（见模块顶部 ``LOCK ORDER``）。
        """
        async with _transaction(self._conn):
            row = await self._conn.fetchrow(
                """
                SELECT subject_org_id, subject_kind, material_scope, report_kind,
                       fiscal_year, mapping_key, applicability_status, due_at,
                       current_document_version_id, caliber_conflict_candidate, status
                FROM material_slots
                WHERE id = $1
                FOR UPDATE
                """,
                slot_id,
            )
            if row is None:
                return {}

            identity_resolved = slot_identity_is_resolved(
                subject_org_id=row["subject_org_id"],
                subject_kind=row["subject_kind"],
                material_scope=row["material_scope"],
                report_kind=row["report_kind"],
                fiscal_year=row["fiscal_year"],
                mapping_key=row["mapping_key"],
            )
            inferred_analysis, inferred_review = infer_progress_state(row["status"])
            result = derive_slot_status(
                applicability_status=str(row["applicability_status"] or "applicable"),
                due_at=row["due_at"],
                identity_resolved=identity_resolved,
                caliber_conflict=bool(row["caliber_conflict_candidate"]),
                has_current_document=row["current_document_version_id"] is not None,
                analysis_state=analysis_state if analysis_state is not None else inferred_analysis,
                review_state=review_state if review_state is not None else inferred_review,
                now=now,
            )
            await self._conn.execute(
                """
                UPDATE material_slots
                SET status = $2, status_reason = $3, updated_at = NOW()
                WHERE id = $1
                """,
                slot_id,
                result.status,
                result.reason,
            )
        return {"status": result.status, "status_reason": result.reason}

    async def record_source(
        self,
        slot_id: Any,
        *,
        source_kind: str = "manual_upload",
        source_url: Optional[str] = None,
        source_page_title: Optional[str] = None,
        source_site: Optional[str] = None,
        published_at: Optional[datetime] = None,
        source_page_hash: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """登记材料来源。

        财政年度与发布日期在这里是两列，物理上不可能混用——
        "2024 年度决算发布于 2025 年"因此不会被写成 2025 年度材料。
        """
        row = await self._conn.fetchrow(
            """
            INSERT INTO material_sources (
                slot_id, source_kind, source_url, source_page_title,
                source_site, published_at, source_page_hash
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (slot_id, COALESCE(source_url, '')) DO UPDATE SET
                source_kind = EXCLUDED.source_kind,
                source_page_title = COALESCE(EXCLUDED.source_page_title, material_sources.source_page_title),
                source_site = COALESCE(EXCLUDED.source_site, material_sources.source_site),
                published_at = COALESCE(EXCLUDED.published_at, material_sources.published_at),
                source_page_hash = COALESCE(EXCLUDED.source_page_hash, material_sources.source_page_hash),
                last_checked_at = NOW(),
                updated_at = NOW()
            RETURNING id, slot_id, source_kind, source_url, published_at
            """,
            slot_id,
            source_kind,
            source_url,
            source_page_title,
            source_site,
            published_at,
            source_page_hash,
        )
        return dict(row) if row is not None else None

    async def mark_not_applicable(
        self, slot_key: str, *, note: str, apply: bool = True
    ) -> Dict[str, Any]:
        """人工确认"本年度无此材料"。

        ``note`` 必填且不可为空：把材料判为不适用会直接把它从"应收"里拿掉，
        没有依据的改判无法复核，等于用一个空字段掩盖一个消失的材料。

        本方法只写"适用性"这一条**事实**，最终状态交给统一状态机推导。
        这不是形式主义：身份未确认的槽位即使用户标了不适用，状态机仍会把它
        留在 ``mapping_required``——"这份材料不适用"和"我们不知道这是哪份材料"
        是两个不同的问题，后者不能借前者绕过。
        """
        text = str(note or "").strip()
        if not text:
            raise ValueError("标记不适用必须给出依据，note 不能为空")

        if not apply:
            return {"applied": False, "slot_key": slot_key, "note": text}

        async with _transaction(self._conn):
            row = await self._conn.fetchrow(
                """
                UPDATE material_slots
                SET applicability_status = 'not_applicable',
                    applicability_note = $2,
                    updated_at = NOW()
                WHERE slot_key = $1
                RETURNING id
                """,
                slot_key,
                text,
            )
            if row is None:
                return {}
            # 状态机是唯一权威：这里不直接写 status。
            # 不传分析/复核进度是安全的——状态机里 not_applicable 的判定排在
            # 文档与进度分支之前，进度取值不会影响本场景的结论。
            await self.refresh_status(row["id"])
            updated = await self._conn.fetchrow(
                """
                SELECT id, slot_key, status, status_reason, applicability_status
                FROM material_slots
                WHERE id = $1
                """,
                row["id"],
            )
        return dict(updated) if updated is not None else {}

    # ---- 读取 ---------------------------------------------------------------

    async def get_slot_by_id(self, slot_id: Any) -> Optional[Dict[str, Any]]:
        row = await self._conn.fetchrow(
            """
            SELECT s.*, v.file_hash AS current_file_hash,
                   v.original_filename AS current_filename,
                   v.created_at AS current_version_created_at
            FROM material_slots AS s
            LEFT JOIN fiscal_document_versions AS v
                   ON v.id = s.current_document_version_id
            WHERE s.id = $1
            """,
            slot_id,
        )
        return dict(row) if row is not None else None

    async def get_slot_by_key(self, slot_key: str) -> Optional[Dict[str, Any]]:
        row = await self._conn.fetchrow(
            """
            SELECT s.*, v.file_hash AS current_file_hash,
                   v.original_filename AS current_filename,
                   v.created_at AS current_version_created_at
            FROM material_slots AS s
            LEFT JOIN fiscal_document_versions AS v
                   ON v.id = s.current_document_version_id
            WHERE s.slot_key = $1
            """,
            slot_key,
        )
        return dict(row) if row is not None else None

    async def list_versions(self, slot_id: Any) -> List[Dict[str, Any]]:
        """槽位下的全部文件版本。

        "文件版本"与"分析运行"在本层是两个概念：本方法只返回版本，
        运行记录由处理队列侧提供。同一版本可以有多条运行记录。
        """
        rows = await self._conn.fetch(
            """
            SELECT id, file_hash, original_filename, storage_key,
                   file_size_bytes, created_at
            FROM fiscal_document_versions
            WHERE slot_id = $1
            ORDER BY created_at DESC, id DESC
            """,
            slot_id,
        )
        return [dict(row) for row in rows]


# ---- 便捷入口（供结构化入库等调用方直接用） ---------------------------------


async def allocate_for_document(
    conn: Any,
    *,
    metadata: Dict[str, Any],
    checksum: Optional[str] = None,
    org_records: Optional[List[Dict[str, Any]]] = None,
    document_version_id: Optional[Any] = None,
) -> Dict[str, Any]:
    """判定并落库一条材料，返回可写入结构化入库结果的摘要。

    槽位 upsert、版本绑定、当前版本指针推进、状态刷新在**同一个事务**内完成。
    此前它们是四个独立语句，中间任何一步失败都会留下部分写入：槽位建了但没绑版本、
    或者版本绑了但指针没动。部分写入比整体失败危险得多——失败会报警，
    部分写入只会安静地留下一个"看起来正常"的错误状态。

    事务内抛出的错误会整体回滚，再由 ``safe_allocate_for_document`` 转成摘要，
    因此"旁路故障不阻断主分析"的设计没有被事务化破坏。
    """
    if conn is None:
        return {"status": "skipped", "reason": "database_unavailable"}

    records = org_records if org_records is not None else resolver.load_org_records()
    decision = resolver.decide_slot_allocation(
        metadata=metadata, org_records=records, checksum=checksum
    )

    if decision.status == resolver.DECISION_UNALLOCATABLE or decision.identity is None:
        return {
            "status": decision.status,
            "reason": decision.reason,
            "reason_label": resolver.REASON_LABELS.get(decision.reason, decision.reason),
        }

    service = MaterialSlotService(conn)
    async with _transaction(conn):
        row = await service.upsert_from_decision(
            decision, caliber=_caliber_from_metadata(metadata)
        )
        if row is None:
            return {"status": "skipped", "reason": "slot_not_written"}

        summary: Dict[str, Any] = {
            "status": decision.status,
            "reason": decision.reason,
            "reason_label": resolver.REASON_LABELS.get(decision.reason, decision.reason),
            "slot_id": str(row["id"]),
            "slot_key": row["slot_key"],
            "slot_status": row["status"],
            "slot_status_reason": row["status_reason"],
        }

        if document_version_id is not None:
            await service.bind_document_version(row["id"], document_version_id)
            refreshed = await service.refresh_status(row["id"])
            final = await service.get_slot_by_id(row["id"]) or {}
            summary["document_version_id"] = int(document_version_id)
            summary["bound"] = True
            summary["slot_status"] = refreshed.get("status", summary["slot_status"])
            summary["slot_status_reason"] = refreshed.get(
                "status_reason", summary["slot_status_reason"]
            )
            summary["slot_caliber"] = final.get("caliber")
            summary["caliber_conflict_candidate"] = final.get(
                "caliber_conflict_candidate"
            )
        else:
            summary["bound"] = False

    return summary


async def safe_allocate_for_document(
    conn: Any,
    *,
    metadata: Dict[str, Any],
    checksum: Optional[str] = None,
    org_records: Optional[List[Dict[str, Any]]] = None,
    document_version_id: Optional[Any] = None,
) -> Dict[str, Any]:
    """``allocate_for_document`` 的不抛异常包装。

    槽位是材料台账的旁路能力，它的任何故障都不能让 PDF 解析入库失败——
    因此这里吞掉异常并把原因写进返回值。

    但**绑定冲突单独成一种结果**：它是业务事实（这份文件已经属于别的材料），
    不是运行故障。把它混进笼统的 ``error`` 会让人以为重试就能解决，
    而实际上重试必然再冲突一次。返回值里 ``bound`` 恒为 False——
    冲突绝不允许被写成"已绑定"。
    """
    try:
        return await allocate_for_document(
            conn,
            metadata=metadata,
            checksum=checksum,
            org_records=org_records,
            document_version_id=document_version_id,
        )
    except SlotBindingError as exc:
        logger.warning("Material slot binding rejected: %s", exc)
        return {
            "status": "conflict"
            if isinstance(exc, SlotBindingConflict)
            else "error",
            "reason": exc.reason,
            "bound": False,
            "error": str(exc),
            **{key: value for key, value in exc.detail.items()},
        }
    except Exception as exc:  # noqa: BLE001 - 刻意兜住全部异常，见上
        logger.warning("Material slot allocation failed: %s", exc, exc_info=True)
        return {
            "status": "error",
            "reason": "slot_allocation_failed",
            "bound": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _normalize_caliber(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in ("summary", "self", "unknown") else "unknown"


def _resolve_caliber(
    existing_caliber: Any,
    existing_candidate: Any,
    incoming_caliber: Any,
) -> tuple:
    """算出本次写入应当保存的口径与冲突候选。

    规则（与设计文档一致：口径不进唯一键，但已确认口径不许被静默改写）：

    ==========================================  ==========================
    existing            incoming                结果
    ==========================================  ==========================
    unknown             已确认                 采用 incoming
    unknown             unknown                保持 unknown
    已确认              unknown                保留 existing（识别失败不改口径）
    已确认              与 existing 相同         保持 existing
    已确认              与 existing 不同         **保留 existing**，
                                                并把 incoming 记为冲突候选
    ==========================================  ==========================

    冲突候选一旦记下就**不会被后续一致观测清除**：后来又一次识别成 existing
    并不能证明第一次的相反观测是错的，只说明矛盾还在。清除只能由显式的
    人工裁决完成（本轮不提供该入口，因此候选会一直可见）。
    """
    incoming = _normalize_caliber(incoming_caliber)
    current = _normalize_caliber(existing_caliber) if existing_caliber is not None else "unknown"
    candidate = _normalize_candidate(existing_candidate)

    if current == "unknown":
        return (incoming, candidate)
    if incoming == "unknown" or incoming == current:
        return (current, candidate)
    return (current, candidate or incoming)


def _normalize_candidate(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in _CONFIRMED_CALIBERS else ""


def _caliber_from_metadata(metadata: Dict[str, Any]) -> str:
    """从画像里取口径。取不到就是 unknown，不猜。"""
    profile = metadata.get("document_profile") if isinstance(metadata, dict) else None
    if isinstance(profile, dict):
        field = profile.get("caliber")
        if isinstance(field, dict):
            return _normalize_caliber(field.get("value"))
    return "unknown"
