"""槽位的持久化：把归属判定结果落库，并维护文件版本指针与状态缓存。

分工
----
- ``material_slot_resolver`` 决定"这份材料属于哪条槽位"（纯函数，
  线上与回填 dry-run 共用）；
- 本模块只负责"把这个结论写进数据库"，不做任何归属判断。

这样拆开的好处是：判断规则可以单测，写库路径可以单独做隔离与幂等，
两者不会互相污染。

幂等
----
``upsert_from_decision`` 用 ``ON CONFLICT (slot_key) DO UPDATE``，
同一份材料重复分析、重复上传都只更新既有槽位，绝不会多出业务材料——
这正是"重新分析不得生成新业务材料"的落点。

永不阻断主流程
--------------
``safe_allocate_for_document`` 吞掉全部异常并返回带 ``status="error"`` 的结果。
材料台账是旁路能力，它的故障不允许让 PDF 解析/入库失败。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.schemas.material_slot import (
    SlotIdentity,
)
from src.services import material_slot_resolver as resolver
from src.services.material_slot_status import (
    ANALYSIS_NOT_STARTED,
    REVIEW_NONE,
    derive_slot_status,
)

logger = logging.getLogger(__name__)


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

        ``status`` 由状态机推导后一并写入：新槽位没有文件版本，
        因此要么是 ``mapping_required``（身份未确认），
        要么是 ``not_due``（身份确认但尚未上传）。
        """
        if decision.identity is None:
            return None

        status_result = derive_slot_status(
            applicability_status="applicable",
            due_at=due_at,
            identity_resolved=decision.identity.is_resolved,
            has_current_document=False,
            analysis_state=ANALYSIS_NOT_STARTED,
            review_state=REVIEW_NONE,
        )

        identity: SlotIdentity = decision.identity
        row = await self._conn.fetchrow(
            """
            INSERT INTO material_slots (
                slot_key,
                jurisdiction_org_id, jurisdiction_name,
                department_org_id, department_name,
                subject_org_id, subject_org_name, subject_org_code,
                subject_kind, subject_level, material_scope, caliber,
                fiscal_year, report_kind,
                applicability_status, due_at, expected_source_url,
                status, status_reason, mapping_key
            )
            VALUES (
                $1,
                $2, $3,
                $4, $5,
                $6, $7, $8,
                $9, $10, $11, $12,
                $13, $14,
                'applicable', $15, $16,
                $17, $18, $19
            )
            ON CONFLICT (slot_key) DO UPDATE SET
                -- 已确认的归属不允许被后续的空值抹掉：历史任务常常缺字段，
                -- 用 COALESCE 保留先写入的可靠值。
                jurisdiction_org_id = COALESCE(EXCLUDED.jurisdiction_org_id, material_slots.jurisdiction_org_id),
                jurisdiction_name = COALESCE(EXCLUDED.jurisdiction_name, material_slots.jurisdiction_name),
                department_org_id = COALESCE(EXCLUDED.department_org_id, material_slots.department_org_id),
                department_name = COALESCE(EXCLUDED.department_name, material_slots.department_name),
                subject_org_name = COALESCE(NULLIF(EXCLUDED.subject_org_name, ''), material_slots.subject_org_name),
                subject_org_code = COALESCE(EXCLUDED.subject_org_code, material_slots.subject_org_code),
                -- 口径只在新值确实识别出来时覆盖，避免把已确认口径降级成 unknown
                caliber = CASE WHEN EXCLUDED.caliber = 'unknown' THEN material_slots.caliber ELSE EXCLUDED.caliber END,
                expected_source_url = COALESCE(EXCLUDED.expected_source_url, material_slots.expected_source_url),
                -- 截止时间只填空值：人工设定的截止时间不该被自动流程覆盖
                due_at = COALESCE(material_slots.due_at, EXCLUDED.due_at),
                -- 状态在绑定文件版本时会由 refresh_status 重算，
                -- 这里只在"还没绑定任何版本"时更新，避免把已推进的状态打回起点。
                status = CASE
                    WHEN material_slots.current_document_version_id IS NULL THEN EXCLUDED.status
                    ELSE material_slots.status
                END,
                status_reason = CASE
                    WHEN material_slots.current_document_version_id IS NULL THEN EXCLUDED.status_reason
                    ELSE material_slots.status_reason
                END,
                updated_at = NOW()
            RETURNING id, slot_key, status, status_reason
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
            status_result.status,
            status_result.reason,
            identity.mapping_key,
        )
        return dict(row) if row is not None else None

    async def bind_document_version(
        self, slot_id: Any, document_version_id: Any
    ) -> Optional[Dict[str, Any]]:
        """把文件版本挂到槽位，并把"当前版本"指针推进到较新者。

        指针只在新区版本不早于当前版本时前进：回填历史数据是按目录顺序跑的，
        没有这条保护，一次乱序回填就会把"当前版本"退回到旧文件。
        """
        await self._conn.execute(
            """
            UPDATE fiscal_document_versions
            SET slot_id = $2
            WHERE id = $1
            """,
            document_version_id,
            slot_id,
        )
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
                          AND cur.created_at <= v.created_at
                    )
                  )
            """,
            slot_id,
            document_version_id,
        )
        return await self.get_slot_by_id(slot_id)

    async def refresh_status(
        self,
        slot_id: Any,
        *,
        analysis_state: str = ANALYSIS_NOT_STARTED,
        review_state: str = REVIEW_NONE,
        now: Optional[datetime] = None,
    ) -> Dict[str, str]:
        """按当前事实重算并落库槽位状态。

        状态机的输入全部来自事实列（是否已有当前版本、适用性、截止时间），
        因此本方法可以在任何时刻重放，结果只取决于库里的真实状态。
        """
        row = await self._conn.fetchrow(
            """
            SELECT applicability_status, due_at, current_document_version_id, mapping_key
            FROM material_slots
            WHERE id = $1
            """,
            slot_id,
        )
        if row is None:
            return {}

        identity_resolved = not str(row["mapping_key"] or "")
        result = derive_slot_status(
            applicability_status=str(row["applicability_status"] or "applicable"),
            due_at=row["due_at"],
            identity_resolved=identity_resolved,
            has_current_document=row["current_document_version_id"] is not None,
            analysis_state=analysis_state,
            review_state=review_state,
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
        本方法只改当前材料实例的适用性，不触碰义务目录。
        """
        text = str(note or "").strip()
        if not text:
            raise ValueError("标记不适用必须给出依据，note 不能为空")

        if not apply:
            return {"applied": False, "slot_key": slot_key, "note": text}

        row = await self._conn.fetchrow(
            """
            UPDATE material_slots
            SET applicability_status = 'not_applicable',
                applicability_note = $2,
                status = 'not_applicable',
                status_reason = 'applicability_marked_not_applicable',
                updated_at = NOW()
            WHERE slot_key = $1
            RETURNING id, slot_key, status, status_reason
            """,
            slot_key,
            text,
        )
        return dict(row) if row is not None else {}

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

    没有可用连接或身份无法判定时返回说明性结果，不抛异常。
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
        await service.refresh_status(row["id"])
        summary["document_version_id"] = int(document_version_id)
        summary["bound"] = True
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
    因此这里吞掉异常并把原因写进返回值。调用方据此在结果里留下痕迹，
    不会出现"槽位没建、流程却显示一切正常"却查不到原因的情况。
    """
    try:
        return await allocate_for_document(
            conn,
            metadata=metadata,
            checksum=checksum,
            org_records=org_records,
            document_version_id=document_version_id,
        )
    except Exception as exc:  # noqa: BLE001 - 刻意兜住全部异常，见上
        logger.warning("Material slot allocation failed: %s", exc, exc_info=True)
        return {
            "status": "error",
            "reason": "slot_allocation_failed",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _normalize_caliber(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in ("summary", "self", "unknown") else "unknown"


def _caliber_from_metadata(metadata: Dict[str, Any]) -> str:
    """从画像里取口径。取不到就是 unknown，不猜。"""
    profile = metadata.get("document_profile") if isinstance(metadata, dict) else None
    if isinstance(profile, dict):
        field = profile.get("caliber")
        if isinstance(field, dict):
            return _normalize_caliber(field.get("value"))
    return "unknown"
