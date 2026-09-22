"""全局材料搜索读接口的统一契约（WP2-C）。

为什么单独一个模块
------------------
WP2-A 的 ``material_ledger.py`` 回答"台账上有什么"，WP2-B 的
``material_detail.py`` 回答"这条材料是什么状态"。WP2-C 的搜索回答的是
"我要找的那份材料在哪" —— 它复用两者已经定好的词汇（``status`` /
``report_kind`` / ``data_basis`` / ``pagination``），但多出一类**本轮特有**的字段：
"为什么这条结果会出现在这里"（``matched_*``）。

「命中原因」必须由后端给出，不能在展示层反推
--------------------------------------------
搜索结果最危险的形态是"看起来毫无关系"。用户搜 ``规划和自然资源局 本部 2024 决算``，
返回一条主体名不含"本部"的材料时，界面必须能说清"命中的是关系（本部）"而不是
让用户自己猜。因此每一条结果都带 ``matched_fields``（机器可读码），
中文文案在展示层（``app/lib/materialSearchPresentation.ts``）—— 与 WP2-A/B
同一条纪律：后端翻译会让同一原因在两端各写一份。

三个不可让步的口径
------------------
1. **搜索的业务对象是 Material Slot，不是 Job / PDF / 分析运行。** 用户拿
   ``job-abc123`` 来搜，可以精确关联到某个文件版本再定位到槽位，但返回的
   仍然是槽位（见 §九）。禁止把全局搜索实现成"下载全部任务再在前端过滤"。
2. **历史版本的命中不能改写当前真值。** 命中历史文件名时，``matched_filename``
   会如实给出历史文件名，同时 ``matched_version_is_current=false``；
   而 ``current_document_version_id`` / ``current_filename`` 永远是槽位上的
   当前指针（§二十一/§二十二）。详情页因此不会被搜索结果"切回"旧版本结论。
3. **不可判断时给 null，不给猜测。** ``review_candidate`` 只有"当前版本 →
   精确运行 → 该运行你有权访问"三条同时成立时才有值（§二十六/§二十七）。
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from src.schemas.material_detail import SLOT_LINKAGE_BASIS
from src.schemas.material_ledger import (
    ApplicabilityStatus,
    Caliber,
    MaterialLedgerMeta,
    MaterialListMeta,
    MaterialScope,
    MaterialStatus,
    Relationship,
    SlotReportKind,
    SubjectKind,
    UtcDatetime,
    build_list_meta,
)

#: 搜索命中的字段码（机器可读）。展示层映射成中文，后端不出中文。
#:
#: - ``current_filename`` / ``historical_filename`` 分开：用户必须一眼看出
#:   命中的是**历史版本**的文件名，否则会以为当前材料就叫这个名字（§四十五）；
#: - ``job_id``：命中的是处理任务 ID（精确关联，§二十三）；
#: - ``relationship``：命中的是"本部/本级"这类层级修饰词（§十六）。
SearchMatchedField = Literal[
    "jurisdiction",
    "department",
    "unit",
    "current_filename",
    "historical_filename",
    "job_id",
    "fiscal_year",
    "report_kind",
    "relationship",
]

#: 「本部/本级」这条关系约束的解析结果。
#:
#: ``unavailable`` 是 fail-closed 的显式出口（§十八）：组织目录不可用时，
#: **不能**把"主体层级是单位"当成"本部"（直属单位也是单位），
#: 因此这条约束命中不了任何槽位，并且这个事实必须在 meta 里可见 ——
#: 否则用户看到的是"没有结果"，而真相是"这次没法按关系筛"。
RelationshipResolution = Literal["not_requested", "resolved", "unavailable"]

#: 搜索结果的关联口径，与 WP2-B 的处理记录逐字一致（同一个 jsonb 路径，
#: 不允许出现第二套"运行属于哪份文件"的判定）。
SEARCH_LINKAGE_BASIS = SLOT_LINKAGE_BASIS

#: 没有 ``structured_ingest.document_version_id`` 的 legacy 任务不参与搜索命中，
#: 也不会被猜到某个槽位上（§二十四）。
LEGACY_UNLINKED_JOB_MATCHES_EXCLUDED = True

#: 分页默认值/上限。上限 50 比列表接口（100）更小：搜索一次要打两条 SQL，
#: 单页结果越大，"每条结果再查一次"的诱惑就越大（§三十二/§六十六）。
DEFAULT_SEARCH_PAGE_SIZE = 20
MAX_SEARCH_PAGE_SIZE = 50


class MaterialSearchReviewCandidate(BaseModel):
    """"进入复核"所需的最小事实：只有 job 与状态。

    刻意**不**带结果内容、不带动不动就几百 KB 的 analysis payload：
    搜索结果是导航入口，不是结论展示位。该任务的状态是否允许进复核，
    由前端既有的唯一判定 ``resolveReviewEntryDecision()`` 决定（§二十八）；
    后端只回答"这个人有没有权限看这个任务"（§二十六）。
    """

    job_uuid: str
    status: str


class MaterialSearchItem(BaseModel):
    """一条搜索结果（= 一条 Material Slot）。

    ``matched_*`` 三组字段的语义必须一起读：

    - 命中**当前**文件名：``matched_version_is_current=true``；
    - 命中**历史**文件名：``matched_version_is_current=false``，
      但 ``current_document_version_id`` / ``current_filename`` 仍是当前指针；
    - 命中 Job：``matched_job_uuid`` + ``matched_job_version_is_current``
      （历史版本的 Job 不得成为 ``review_candidate``，§四十七）。
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

    #: 主体在部门矩阵中的层级关系（展示用，复用 WP2-A 的 ``relationship_of``）。
    #: 用户搜"本部"时，这里是让结果自证"为什么它算本部"的字段。
    relationship: Relationship

    fiscal_year: Optional[int] = None
    report_kind: SlotReportKind
    caliber: Caliber
    status: MaterialStatus
    status_reason: Optional[str] = None
    applicability_status: ApplicabilityStatus = "applicable"

    #: 当前文件版本指针与它的文件名（唯一真值 ``current_document_version_id``）。
    current_document_version_id: Optional[int] = None
    current_filename: Optional[str] = None

    #: 命中原因（机器可读码，见 ``SearchMatchedField``）。
    matched_fields: List[SearchMatchedField] = Field(default_factory=list)

    #: 命中的文件版本。非 null 表示"这次命中的是文件名"；
    #: 三个字段要么一起有值、要么一起为 null。
    matched_filename: Optional[str] = None
    matched_document_version_id: Optional[int] = None
    matched_version_is_current: Optional[bool] = None

    #: 命中的处理任务（精确关联）。``matched_job_version_is_current=false``
    #: 表示命中的是历史版本上的运行 —— 界面必须显式标注（§四十七）。
    matched_job_uuid: Optional[str] = None
    matched_job_version_is_current: Optional[bool] = None

    #: 可否进入复核。为 null 时**只**表示"这次不能安全判定"（无运行 /
    #: 无权限 / 非当前版本），界面此时只保留「打开材料」（§二十九）。
    review_candidate: Optional[MaterialSearchReviewCandidate] = None

    updated_at: Optional[UtcDatetime] = None


class MaterialSearchData(BaseModel):
    items: List[MaterialSearchItem]


class MaterialSearchMeta(MaterialListMeta):
    """搜索响应的 meta：在列表 meta 上补充"这次是怎么搜的"。

    ``query`` 回显的是**去空白后的原始查询串**，不是解析结果：
    解析（年度/文种/关系/token）属于服务端实现细节，回显它会让前端有机会
    按另一套规则复现分页。前端要展示"为什么命中"，用每条的 ``matched_fields``。
    """

    query: str
    relationship_resolution: RelationshipResolution = "not_requested"
    linkage_basis: Literal["structured_document_version_id"] = SEARCH_LINKAGE_BASIS
    legacy_unlinked_job_matches_excluded: bool = LEGACY_UNLINKED_JOB_MATCHES_EXCLUDED


class MaterialSearchResponse(BaseModel):
    ok: Literal[True] = True
    data: MaterialSearchData
    meta: MaterialSearchMeta


def build_search_meta(
    *,
    query: str,
    page: int,
    page_size: int,
    total: int,
    relationship_resolution: RelationshipResolution = "not_requested",
    generated_at=None,
) -> MaterialSearchMeta:
    """按列表口径构造 meta（分页规则与 WP2-A 列表接口同源，只有一处实现）。"""
    base = build_list_meta(
        page=page, page_size=page_size, total=total, generated_at=generated_at
    )
    return MaterialSearchMeta(
        **base.model_dump(),
        query=query,
        relationship_resolution=relationship_resolution,
    )


__all__ = [
    "SEARCH_LINKAGE_BASIS",
    "LEGACY_UNLINKED_JOB_MATCHES_EXCLUDED",
    "DEFAULT_SEARCH_PAGE_SIZE",
    "MAX_SEARCH_PAGE_SIZE",
    "SearchMatchedField",
    "RelationshipResolution",
    "MaterialSearchReviewCandidate",
    "MaterialSearchItem",
    "MaterialSearchData",
    "MaterialSearchMeta",
    "MaterialSearchResponse",
    "build_search_meta",
    "MaterialLedgerMeta",
]
