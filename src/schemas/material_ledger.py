"""材料台账读接口的统一契约（WP2-A）。

为什么单独建一份契约模块
------------------------
三个 Materials 接口（首页聚合 / 区级主管部门矩阵 / 部门材料矩阵）回答的是同一个问题
的三个视角，字段名与语义必须完全一致。历史上本项目出现过"同一件事在不同接口叫不同名字"
（``pending_review`` 与 ``review_required_count`` 并存）的教训，前端只能靠适配层猜，
最后表现成"两个页面数字对不上"。因此这里把响应结构一次定义清楚，路由层只负责装配，
不允许在 route 里手写 dict。

三条不可让步的口径
------------------
1. **状态只有一套。** ``status`` 取值域直接取自 WP1 的 ``material_slots`` CHECK 约束
   （``src/schemas/material_slot.py`` 的 ``SLOT_STATUSES``），WP2 不新增第二套业务状态。
   ``status_reason`` 原样透出 WP1 写入的原因码，**不做翻译** —— 中文文案属于展示层
   （``app/lib/materialStatusPresentation.ts``），后端翻译会让同一原因在两端各写一遍。
2. **null 与 0 严格区分。** ``null`` = 当前无法计算/不存在；``0`` = 已计算且结果为零；
   ``[]`` = 空集合。因此"没有应收基线"时 ``expected_*`` / ``*_coverage_rate`` 一律
   ``None`` 而不是 0：0 会被读成"一条都不缺"，那是最危险的假结论。
3. **时间一律 ISO 8601 带时区。** 序列化统一走 ``_to_utc_iso``，输出形如
   ``2026-09-21T11:42:00Z``；不允许某个接口给 Unix 时间戳、另一个给本地格式字符串。

字段全集与 §六 的对应关系
-------------------------
``ExpectedCoverageFields`` 收拢了"应收基线建立之后才有意义"的七个字段
（§六 逐条列出）。它们在 ``expected_materials_ready=False`` 时必须整体为 ``null``；
先以显式 null 出现，是为了让"无法计算"在协议层只有一种表达，前端据此渲染 ``—``，
而不是各页面自行把缺失换成 0 或空串。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

#: 数据口径：本轮所有统计**只**基于 ``material_slots`` 里已存在的槽位。
#: WP9 引入应收材料基线后会增加 ``expected_materials``，届时按接口参数切换，
#: 本轮不提前实现，也不允许任何接口默认宣称"应收已建立"。
DATA_BASIS_EXISTING_SLOTS_ONLY = "existing_slots_only"

#: 应收材料基线是否已建立。WP9 之前恒为 False。
#: 单独抽成常量而不是散在各处的字面量：这两个值（data_basis / expected_materials_ready）
#: 是前端"完整率显示 —"与顶部口径提示的唯一依据，必须只有一个出口。
EXPECTED_MATERIALS_READY = False

#: 状态取值域，与 WP1 ``material_slots.status`` 的 CHECK 约束逐字一致。
MATERIAL_STATUSES: tuple = (
    "not_due",
    "missing",
    "uploaded",
    "processing",
    "review_required",
    "reviewing",
    "completed",
    "not_applicable",
    "mapping_required",
    "failed",
)

MaterialStatus = Literal[
    "not_due",
    "missing",
    "uploaded",
    "processing",
    "review_required",
    "reviewing",
    "completed",
    "not_applicable",
    "mapping_required",
    "failed",
]

#: 文种过滤参数。``all`` 只是查询参数，**不是**数据库里的业务状态：
#: 它表示"预算与决算都查"，不会写进任何一行。
ReportKindFilter = Literal["all", "budget", "final"]

SubjectKind = Literal["department", "unit", "government", "unknown"]
MaterialScope = Literal["department_summary", "unit_self", "government", "unknown"]
SlotReportKind = Literal["budget", "final", "unknown"]
Caliber = Literal["summary", "self", "unknown"]
ApplicabilityStatus = Literal["applicable", "not_applicable", "unresolved"]

#: 主体在部门材料矩阵中的层级关系（**仅展示用**，不落库、不制造新的业务真值）。
#: 由现有字段推导：``subject_org_id`` / ``department_org_id`` / ``subject_kind`` /
#: ``material_scope``（见 ``material_ledger_query_service._relationship_of``）。
#: 第四个取值 ``relationship_unknown`` 是刻意的：主体层级认不出来时，
#: 宁可在界面上显式承认"关系待确认"，也不猜一个"直属单位"。
Relationship = Literal["department_summary", "head_unit", "subordinate_unit", "relationship_unknown"]


def _to_utc_iso(value: Optional[datetime]) -> Optional[str]:
    """统一的时间序列化口径：UTC、ISO 8601、以 ``Z`` 结尾。

    朴素时间按 UTC 理解（数据库列是 TIMESTAMPTZ，取回来一定带时区；
    测试替身可能给朴素时间，这里明确按 UTC 处理而不是按本地时区猜）。
    """
    if value is None:
        return None
    moment = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


UtcDatetime = Annotated[datetime, PlainSerializer(_to_utc_iso, return_type=Optional[str])]


def empty_status_counts() -> Dict[str, int]:
    """全部状态计数为 0 的字典。

    列表接口里"某个部门没有预算材料"是**已计算的零**（不是"无法计算"），
    因此必须给出完整的 10 个键、值全为 0，而不是省略字段或给 null。
    """
    return {status: 0 for status in MATERIAL_STATUSES}


class MaterialStatusCounts(BaseModel):
    """"统一统计对象"的状态部分：10 个状态一个不少。

    ``extra="forbid"`` 是刻意的：数据库 CHECK 约束与本取值域一旦不同步
    （例如新增状态却忘了加到这里），构造会直接失败并暴露出来，
    远好过静默丢掉一类计数、让"分类之和不等于总数"这种最难查的缺陷流到界面上。
    """

    model_config = ConfigDict(extra="forbid")

    not_due: int
    missing: int
    uploaded: int
    processing: int
    review_required: int
    reviewing: int
    completed: int
    not_applicable: int
    mapping_required: int
    failed: int


class ExpectedCoverageFields(BaseModel):
    """应收基线相关字段（WP9 之前恒为 ``None``）。

    §六 逐条列出的七个字段都收在这里，避免"这个接口给了 null、那个接口给 0"。
    ``expected_materials_ready=True`` 之后，由 WP9 负责填充真实值；
    在那之前，任何地方把它们写成 0 都是把"未知"伪装成"一条不缺"。
    """

    expected_total: Optional[int] = Field(default=None, description="应收材料总数；基线未建立时为 null")
    expected_budget_total: Optional[int] = Field(default=None, description="应收预算材料数；基线未建立时为 null")
    expected_final_total: Optional[int] = Field(default=None, description="应收决算材料数；基线未建立时为 null")
    coverage_rate: Optional[float] = Field(default=None, description="完整率（0-1）；基线未建立时为 null")
    budget_coverage_rate: Optional[float] = Field(default=None, description="预算完整率；基线未建立时为 null")
    final_coverage_rate: Optional[float] = Field(default=None, description="决算完整率；基线未建立时为 null")
    missing_expected_total: Optional[int] = Field(default=None, description="应收未收总数；基线未建立时为 null")


class MaterialLedgerMeta(BaseModel):
    """所有 ``/api/materials/*`` 响应共享的 meta。

    只带三个字段（§四）：口径标识、应收基线是否就绪、生成时间。
    顶部提示文案与"完整率显示 —"的判定都以此为准 ——
    前端据 ``expected_materials_ready`` 选择文案，不自行推断口径。
    """

    data_basis: Literal["existing_slots_only"]
    expected_materials_ready: bool
    generated_at: UtcDatetime


class PaginationMeta(BaseModel):
    """分页信息。``total`` 是过滤后的总数，``total_pages`` 在无数据时为 0。"""

    page: int
    page_size: int
    total: int
    total_pages: int


class MaterialListMeta(MaterialLedgerMeta):
    """列表接口的 meta：在共享 meta 上增加分页。"""

    pagination: PaginationMeta


class MaterialSlotSummary(BaseModel):
    """槽位摘要：三个接口共用的唯一 DTO。

    命名统一为 ``slot_id``，不允许在不同接口里改叫 ``id`` / ``material_id`` /
    ``material_slot_id`` —— 前端因此可以只写一个类型。
    """

    slot_id: str
    slot_key: str

    jurisdiction_id: Optional[str] = None
    jurisdiction_name: Optional[str] = None

    department_org_id: Optional[str] = None
    department_name: Optional[str] = None

    subject_org_id: str
    subject_org_name: str

    subject_kind: SubjectKind
    material_scope: MaterialScope

    fiscal_year: Optional[int] = None
    report_kind: SlotReportKind

    caliber: Caliber
    caliber_conflict_candidate: Optional[Literal["summary", "self"]] = Field(
        default=None,
        description="已确认口径与后来识别到的口径冲突时的候选值；非 null 即表示待人工裁决",
    )

    status: MaterialStatus
    status_reason: Optional[str] = Field(
        default=None, description="WP1 状态机写入的原因码（原样透出，不在后端翻译）"
    )

    applicability_status: ApplicabilityStatus = "applicable"
    applicability_note: Optional[str] = None

    due_at: Optional[UtcDatetime] = None
    current_document_version_id: Optional[int] = None

    formal_issue_count: Optional[int] = Field(
        default=None,
        description=(
            "当前文件版本上已确认的正式问题数。WP3 接通复核生命周期之前恒为 null —— "
            "禁止用 0 冒充'没有问题'：0 是'查过且确实没有'，null 是'现在算不出来'"
        ),
    )

    updated_at: Optional[UtcDatetime] = None


class MaterialCoverageSummary(ExpectedCoverageFields):
    """全局口径统计（首页 KPI 与第一屏的数字来源）。"""

    slot_total: int
    status_counts: MaterialStatusCounts
    #: 按文种拆分。注意：``slot_total = budget_total + final_total + unknown_kind_total``，
    #: 文种未识别/存在冲突的槽位不会被塞进预算或决算任何一边。
    budget_total: int
    final_total: int
    unknown_kind_total: int
    #: 截止时间未知（``due_at IS NULL``）的槽位数。它必须独立可见：
    #: 这些槽位不能计入"逾期未上传"，也不该被读成"尚未到期"。
    due_at_unknown: int
    #: 未归入任何行政区划的槽位数。区县卡片只覆盖有 jurisdiction 的槽位，
    #: 这个计数保证"剩下那些去哪了"在首页就能看到，而不是凭空消失。
    jurisdiction_unknown_total: int


class DistrictCoverageItem(ExpectedCoverageFields):
    """区县卡片 / 区县一级的统计。"""

    district_id: str
    district_name: str
    slot_total: int
    status_counts: MaterialStatusCounts
    budget_total: int
    final_total: int
    unknown_kind_total: int
    due_at_unknown: int
    updated_at: Optional[UtcDatetime] = None


class MaterialCoverageData(BaseModel):
    summary: MaterialCoverageSummary
    #: 按 ``district_name`` 稳定排序（同名时以 id 兜底）。
    districts: List[DistrictCoverageItem]


class MaterialCoverageResponse(BaseModel):
    ok: Literal[True] = True
    data: MaterialCoverageData
    meta: MaterialLedgerMeta


class DepartmentScopeStat(BaseModel):
    """某个文种口径下的统计（预算 / 决算各一份）。"""

    slot_total: int
    status_counts: MaterialStatusCounts


class DepartmentMatrixItem(ExpectedCoverageFields):
    """区级主管部门矩阵的一行。"""

    #: 允许为 null：区级政府本级材料没有主管部门（``department_org_id IS NULL``），
    #: 但它们真实存在于该区，必须能被看见，不能因为"没有部门"就从矩阵里消失。
    department_id: Optional[str] = None
    department_name: Optional[str] = None

    subject_count: int
    slot_total: int
    status_counts: MaterialStatusCounts

    budget: DepartmentScopeStat
    final: DepartmentScopeStat

    unknown_kind_total: int
    missing: int
    not_due: int
    due_at_unknown: int
    updated_at: Optional[UtcDatetime] = None


class DistrictRef(BaseModel):
    district_id: str
    district_name: str


class DepartmentRef(BaseModel):
    department_id: str
    department_name: str
    jurisdiction_id: Optional[str] = None
    jurisdiction_name: Optional[str] = None


class DistrictDepartmentMatrixData(BaseModel):
    district: DistrictRef
    #: 按 ``department_name`` 稳定排序（未归入主管部门的行排在最后）。
    items: List[DepartmentMatrixItem]


class DistrictDepartmentMatrixResponse(BaseModel):
    ok: Literal[True] = True
    data: DistrictDepartmentMatrixData
    meta: MaterialListMeta


class SubjectSlotRef(BaseModel):
    """某主体在某文种上的槽位引用。

    ``exists=false`` 的语义必须精确：**只**表示"当前 material_slots 里没有这条槽位"。
    它既不表示"应该有但缺失"，也不表示"未上传" —— 应收基线未建立时，
    没有任何依据把"没有槽位"解释成"缺失"。
    """

    exists: bool
    slot: Optional[MaterialSlotSummary] = None


class DepartmentSubjectRow(BaseModel):
    """部门材料矩阵的一个主体行。"""

    subject_org_id: str
    subject_org_name: str
    subject_kind: SubjectKind
    relationship: Relationship

    budget: SubjectSlotRef
    final: SubjectSlotRef
    #: 文种未识别的槽位（``report_kind='unknown'``）。它们既不是预算也不是决算，
    #: 若只提供 budget/final 两个位置，这些材料会在部门页上凭空消失 ——
    #: 而它们恰恰是最需要人工确认的一批（文种冲突/未识别）。
    unclassified: SubjectSlotRef


class DepartmentGroupSet(BaseModel):
    """按主体层级分组的三个列表 + 一个"关系待确认"兜底组。"""

    department_summary: List[DepartmentSubjectRow]
    head_unit: List[DepartmentSubjectRow]
    subordinate_units: List[DepartmentSubjectRow]
    #: 主体层级或材料范围无法确认时落在这里（不猜成"直属单位"）。
    #: 正常情况下恒为空；非空即表示有材料需要人工确认归属。
    relationship_unknown: List[DepartmentSubjectRow]


class DepartmentMatrixData(BaseModel):
    department: DepartmentRef
    fiscal_year: int
    groups: DepartmentGroupSet


class DepartmentMatrixResponse(BaseModel):
    ok: Literal[True] = True
    data: DepartmentMatrixData
    meta: MaterialLedgerMeta


def build_meta(generated_at: Optional[datetime] = None) -> MaterialLedgerMeta:
    return MaterialLedgerMeta(
        data_basis=DATA_BASIS_EXISTING_SLOTS_ONLY,
        expected_materials_ready=EXPECTED_MATERIALS_READY,
        generated_at=generated_at or datetime.now(timezone.utc),
    )


def build_list_meta(
    *, page: int, page_size: int, total: int, generated_at: Optional[datetime] = None
) -> MaterialListMeta:
    total_pages = (total + page_size - 1) // page_size if page_size > 0 and total > 0 else 0
    return MaterialListMeta(
        data_basis=DATA_BASIS_EXISTING_SLOTS_ONLY,
        expected_materials_ready=EXPECTED_MATERIALS_READY,
        generated_at=generated_at or datetime.now(timezone.utc),
        pagination=PaginationMeta(page=page, page_size=page_size, total=total, total_pages=total_pages),
    )


__all__ = [
    "DATA_BASIS_EXISTING_SLOTS_ONLY",
    "EXPECTED_MATERIALS_READY",
    "MATERIAL_STATUSES",
    "MaterialStatus",
    "ReportKindFilter",
    "MaterialStatusCounts",
    "ExpectedCoverageFields",
    "MaterialLedgerMeta",
    "MaterialListMeta",
    "PaginationMeta",
    "MaterialSlotSummary",
    "MaterialCoverageSummary",
    "DistrictCoverageItem",
    "MaterialCoverageData",
    "MaterialCoverageResponse",
    "DepartmentScopeStat",
    "DepartmentMatrixItem",
    "DistrictRef",
    "DepartmentRef",
    "DistrictDepartmentMatrixData",
    "DistrictDepartmentMatrixResponse",
    "SubjectSlotRef",
    "DepartmentSubjectRow",
    "DepartmentGroupSet",
    "DepartmentMatrixData",
    "DepartmentMatrixResponse",
    "UtcDatetime",
    "build_meta",
    "build_list_meta",
    "empty_status_counts",
]
