"""复核上下文解析：``job_uuid → 文件版本 → 槽位`` 与"当前分析"的精确取值。

为什么单独一个模块
------------------
"这个 job 属于哪条材料槽位"这个问题有两个调用方：复核生命周期服务
（开/完成复核）与分析落库后的槽位状态同步。两者都必须走**同一条**链路：

    analysis_jobs.metadata → structured_ingest.document_version_id
        → fiscal_document_versions.id / slot_id
        → material_slots

把它放在这里，是为了让"禁止猜归属"这条纪律只有一个实现处。

禁止的四种猜法
--------------
按文件名、按组织名、按文件哈希、按时间接近来推断归属，都会把别的单位、
别的年度的运行挂到这条材料上。**比少显示几条更糟**——它看起来完全正常。
因此本模块只认 ``structured_ingest.document_version_id``：取不到就没有上下文，
调用方必须回 409 ``review_context_unavailable``，而不是退化成猜测。

"当前分析"的定义与材料详情页逐字同源
------------------------------------
复用 ``MaterialDetailQueryService.load_version_run_rows`` 的 join 与
``select_current_run`` 的排序：当前分析 = 当前文件版本下按
``COALESCE(completed_at, started_at, updated_at, created_at) DESC, id DESC``
排第一的那次运行。同一份材料在「材料详情」与「审核工作台」上因此不可能
各说一套"当前分析是哪一次"。

运行存在但没有落库结果时**不回退**到上一次运行：那种回退会把"上一版文件的
结论"当成"这一版文件没有正式问题"，是历史假完成的典型来源。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from src.schemas.issues import COMPLETED_JOB_STATUSES, normalize_job_status
from src.schemas.material_detail import (
    ANALYSIS_REASON_NO_CURRENT_VERSION,
    ANALYSIS_REASON_NONE_FOR_CURRENT_VERSION,
)
from src.services.material_detail_query_service import (
    MaterialDetailQueryService,
    _NUMERIC_GUARD,
    _STRUCTURED_VERSION_JSON_PATH,
    count_canonical_formal_findings,
    run_sort_key,
    select_current_run,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReviewSlotLink:
    """一次运行与它所属材料的精确关联。"""

    slot_id: str
    document_version_id: int
    job_uuid: str


@dataclass
class ReviewAnalysis:
    """槽位当前文件版本上的当前分析。

    ``completed`` 的语义严格限定为"运行已跑完**且**结果已落库"：
    - 运行还在 queued/processing → ``completed=False``，原因是结果还没产生；
    - 运行结束但 ``analysis_results`` 没有行（历史任务、落库失败）→
      ``completed=False``。此时**不能**给出 ``formal_issue_count=0``：
      “没有结果”与“算过且没有问题”在界面上必须长得不一样。
    """

    job_uuid: Optional[str] = None
    analysis_revision: Optional[int] = None
    analysis_basis_token: Optional[str] = None
    document_version_id: Optional[int] = None
    status: Optional[str] = None
    completed: bool = False
    unavailable_reason: Optional[str] = ANALYSIS_REASON_NO_CURRENT_VERSION
    result_row: Optional[Dict[str, Any]] = None
    result_meta: Dict[str, Any] = field(default_factory=dict)
    structured_ingest: Dict[str, Any] = field(default_factory=dict)
    formal_issue_count: Optional[int] = None


def _json_value(raw: Any) -> Any:
    """JSONB 列的取值（可能是 dict，也可能是字符串，本仓库未注册 codec）。"""
    import json

    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return raw
        try:
            return json.loads(text)
        except Exception:  # noqa: BLE001 - 解析失败按原值处理
            return raw
    return raw


def _json_dict(raw: Any) -> Dict[str, Any]:
    value = _json_value(raw)
    return value if isinstance(value, dict) else {}


def _int_or_none(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


#: 复核上下文需要的槽位投影。刻意写死列名而不是 ``SELECT s.*``：
#: 复核的门禁要读"身份是否已解决""口径是否冲突"这类事实，用星号选取会让
#: 一次无关的加列/改名静默改变这里的可用性，而门禁的输入必须显式可审计。
SLOT_REVIEW_PROJECTION = """
    id::text AS slot_id,
    current_document_version_id,
    subject_org_id,
    subject_kind,
    material_scope,
    report_kind,
    fiscal_year,
    mapping_key,
    applicability_status,
    caliber_conflict_candidate,
    status,
    status_reason,
    due_at
"""


async def load_slot_row_for_review(conn: Any, slot_id: Any) -> Optional[Dict[str, Any]]:
    """按复核上下文所需的投影读取槽位行（**不加锁**，读锁时机由调用方决定）。

    单独一个读取入口的理由：``lock_slot_row`` 只锁不投影，而门禁需要十几个列。
    若在锁语句里把这些列一起读回来，"锁"和"读到的内容"就绑死了，
    将来加一个门禁输入就得改锁语句——那正是锁顺序容易出错的地方。
    """
    row = await conn.fetchrow(
        f"""
        SELECT {SLOT_REVIEW_PROJECTION}
        FROM material_slots
        WHERE id = $1
        """,
        slot_id,
    )
    return dict(row) if row is not None else None


async def resolve_slot_link_for_job(conn: Any, job_uuid: str) -> Optional[ReviewSlotLink]:
    """按**精确链路**解析一次运行所属的材料槽位。

    关联谓词与材料详情页共用同一组常量（``_NUMERIC_GUARD`` +
    ``_STRUCTURED_VERSION_JSON_PATH``）。写坏的 metadata
    （``document_version_id`` 不是数字）由该守卫排除，不会让整条查询抛 cast 异常。

    槽位为空（``v.slot_id IS NULL``）时返回 ``None``：历史版本、身份不可靠的
    新版本都可能没挂槽位，那不是"归属未知"，而是"这份材料还没有台账身份"。
    """
    normalized = str(job_uuid or "").strip()
    if not normalized:
        return None
    row = await conn.fetchrow(
        f"""
        SELECT j.job_uuid,
               v.id AS document_version_id,
               v.slot_id::text AS slot_id
        FROM analysis_jobs j
        JOIN fiscal_document_versions v
               ON {_NUMERIC_GUARD}
              AND {_STRUCTURED_VERSION_JSON_PATH}::bigint = v.id
        WHERE j.job_uuid = $1
        """,
        normalized,
    )
    if row is None:
        return None
    slot_id = str(row.get("slot_id") or "")
    version_id = _int_or_none(row.get("document_version_id"))
    if not slot_id or version_id is None:
        return None
    return ReviewSlotLink(
        slot_id=slot_id,
        document_version_id=version_id,
        job_uuid=str(row.get("job_uuid") or normalized),
    )


async def resolve_current_analysis(conn: Any, slot_row: Dict[str, Any]) -> ReviewAnalysis:
    """取槽位当前版本上的当前分析（严格绑定当前指针，绝不回退）。"""
    slot_id = str(slot_row.get("slot_id") or "")
    current_pointer = _int_or_none(slot_row.get("current_document_version_id"))
    if current_pointer is None:
        return ReviewAnalysis(unavailable_reason=ANALYSIS_REASON_NO_CURRENT_VERSION)

    service = MaterialDetailQueryService(conn)
    rows = await service.load_version_run_rows(slot_id)

    linked = [
        row
        for row in rows
        if _int_or_none(row.get("document_version_id")) == current_pointer
        and _int_or_none(row.get("id")) is not None
    ]
    current_run = select_current_run(linked)
    if current_run is None:
        return ReviewAnalysis(
            document_version_id=current_pointer,
            unavailable_reason=ANALYSIS_REASON_NONE_FOR_CURRENT_VERSION,
        )

    row = dict(current_run)
    status = str(row.get("status") or "").strip() or None
    normalized_status = normalize_job_status(status)
    metadata = _json_dict(row.get("metadata"))
    result_meta = _json_dict(metadata.get("result_meta"))
    structured_ingest = _json_dict(metadata.get("structured_ingest"))

    if _int_or_none(row.get("result_id")) is None:
        # 运行存在但没有落库结果：可以展示"跑了什么"，但**不能**给问题数。
        return ReviewAnalysis(
            job_uuid=str(row.get("job_uuid") or "") or None,
            analysis_revision=_int_or_none(row.get("analysis_revision")),
            document_version_id=current_pointer,
            status=status,
            completed=False,
            unavailable_reason=ANALYSIS_REASON_NONE_FOR_CURRENT_VERSION,
            result_meta=result_meta,
            structured_ingest=structured_ingest,
        )

    completed = normalized_status in COMPLETED_JOB_STATUSES
    analysis = ReviewAnalysis(
        job_uuid=str(row.get("job_uuid") or "") or None,
        analysis_revision=_int_or_none(row.get("analysis_revision")),
        document_version_id=current_pointer,
        status=status,
        completed=completed,
        unavailable_reason=None if completed else ANALYSIS_REASON_NONE_FOR_CURRENT_VERSION,
        result_row=row,
        result_meta=result_meta,
        structured_ingest=structured_ingest,
    )
    if completed:
        # 只有走到这里（当前版本 → 精确运行 → 已落库结果 → 可读终态）
        # 才允许出现整数 0：0 的含义是"算过且确实没有正式问题"。
        analysis.formal_issue_count = count_canonical_formal_findings(
            row.get("ai_findings"), row.get("rule_findings")
        )
    return analysis


def current_run_sort_key(row: Dict[str, Any]) -> Any:
    """运行排序键的再导出：让测试可以直接对同一口径排序，不必复制表达式。"""
    return run_sort_key(row)


__all__ = [
    "ReviewSlotLink",
    "ReviewAnalysis",
    "SLOT_REVIEW_PROJECTION",
    "resolve_slot_link_for_job",
    "resolve_current_analysis",
    "load_slot_row_for_review",
    "current_run_sort_key",
]
