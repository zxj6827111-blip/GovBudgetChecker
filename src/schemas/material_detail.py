"""材料详情读接口的统一契约（WP2-B）。

为什么单独一个模块
------------------
WP2-A 的三个接口回答的是"台账上有什么"（``material_ledger.py``）；
WP2-B 的四个接口回答的是"这条材料到底是什么状态、谁在什么时候处理过它"。
两者共享同一套词汇（``MaterialSlotSummary`` / ``data_basis`` / ``meta``），
但后者的字段量级与语义面明显不同（版本、来源、运行、检查覆盖），
塞进同一个文件会让"哪个字段属于哪个接口"变得难查。因此本模块**只**定义
WP2-B 的 DTO，并**导入** WP2-A 的共享词汇，保证同一件事仍只有一个名字。

三条与 WP2-A 逐字一致的纪律
---------------------------
1. **null ≠ 0。** ``formal_issue_count`` / ``coverage.summary`` 为 ``None`` 表示
   "现在算不出来"，``0`` 表示"算过且确实是零"。两者在界面上是完全不同的结论：
   ``0`` 会被读成"这份材料查过、没有问题"，而这正是本项目历史上最危险的假完成。
2. **不可计算时整体置空，不做部分填充。** 检查覆盖拿不到就 ``available=false`` +
   ``summary=None`` + ``items=[]``；绝不返回 ``0 / 0`` 或 ``100%``——
   "没有覆盖记录"和"全部覆盖"在数字上不能长得一样。
3. **时间一律 ISO 8601 UTC。** 复用 WP2-A 的 ``_to_utc_iso`` 口径（同一序列化器）。

中文文案不在本模块
------------------
技术状态码（``obligation status`` / ``reason`` / ``severity``）原样输出，
中文映射与业务分组在展示层（``app/lib/materialDetailPresentation.ts``）。
WP2-A 已经定下这条规则：后端翻译会让同一原因在两端各写一份，必然漂移。
业务分组（``completed=自动检查完成`` 等）因此也属于展示层。
"""

from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from src.schemas.material_ledger import (
    DATA_BASIS_EXISTING_SLOTS_ONLY,
    EXPECTED_MATERIALS_READY,
    MaterialLedgerMeta,
    MaterialSlotSummary,
    UtcDatetime,
    build_meta,
)

# ---- 关联口径（协议层常量，供 API 与前端共同引用） --------------------------

#: 处理记录唯一的关联依据：``fiscal_document_versions.id`` ==
#: ``analysis_jobs.metadata.structured_ingest.document_version_id``。
#: 这是 WP1 建立的**精确**关联。按文件名、组织名、创建时间接近、目录名或
#: "最新一个 job"去猜归属，会把别的单位/别的年度的运行挂到这条材料上——
#: 那比少显示几条更糟：它看起来完全正常。
SLOT_LINKAGE_BASIS = "structured_document_version_id"

#: 当前处理记录只包含"能精确关联到文件版本"的运行。
#: 历史 job 若没有 ``document_version_id``，不猜归属，直接不出现在任何 Slot 下。
LEGACY_UNLINKED_RUNS_EXCLUDED = True

#: 检查覆盖不可用时的成因码（§三十七）。
COVERAGE_REASON_NONE_FOR_CURRENT_VERSION = "no_coverage_for_current_document_version"

#: 当前分析不可用时的成因码（§二十）。
ANALYSIS_REASON_NONE_FOR_CURRENT_VERSION = "no_persisted_analysis_for_current_version"

#: 当前分析不可用且 Slot 没有当前版本时的成因码。
#: 与上面那条必须分开："这份文件还没分析"和"这份材料根本没有当前文件"
#: 是两件事，页面上的下一步动作不同（等分析 vs 去上传）。
ANALYSIS_REASON_NO_CURRENT_VERSION = "no_current_document_version"


class MaterialDetailMeta(MaterialLedgerMeta):
    """详情类响应的 meta：在共享 meta 上补充关联口径。

    ``linkage_basis`` 与 ``legacy_unlinked_runs_excluded`` 只对 runs 有意义，
    但统一挂在 meta 上：前端因此不需要按接口各写一套"这份数据是怎么关联的"判断。
    """

    linkage_basis: Literal["structured_document_version_id"] = SLOT_LINKAGE_BASIS
    legacy_unlinked_runs_excluded: bool = LEGACY_UNLINKED_RUNS_EXCLUDED


def build_detail_meta(generated_at=None) -> MaterialDetailMeta:
    base = build_meta(generated_at)
    return MaterialDetailMeta(**base.model_dump())


# ---- 接口四：单位多年度时间轴 ------------------------------------------------


class TimelineUnitRef(BaseModel):
    """单位时间轴的头部信息。

    身份由 ``unit_id`` 唯一确定（= ``material_slots.subject_org_id``）。
    名称只用于显示：组织目录查不到时回退到槽位里的名称快照，再查不到才用 id。
    """

    unit_id: str
    unit_name: str
    #: 主体在组织目录里的层级类型。**原样透出** ``subject_kind``：
    #: 认不出来时是 ``unknown``，界面据此显示"主体类型待确认"，
    #: 而不是替它猜一个"单位"。
    subject_kind: str
    department_id: Optional[str] = None
    department_name: Optional[str] = None
    jurisdiction_id: Optional[str] = None
    jurisdiction_name: Optional[str] = None


class TimelineYearRow(BaseModel):
    """一个财政年度的一行。

    三个位置都是**数组**（§十六）。一个单位 + 一年 + 一个文种，当前确实
    通常只有一条槽位，但可能出现"正式槽位 + ``mapping_required`` 占位槽位"
    并存（身份没定下来的材料按文档临时建槽）。用 ``slot | null`` 会让
    多出来的那条凭空消失，而它恰恰是最需要人工确认的一批。
    """

    fiscal_year: int
    budget_slots: List[MaterialSlotSummary]
    final_slots: List[MaterialSlotSummary]
    #: 文种未识别或存在冲突的槽位。它们既不是预算也不是决算，
    #: 只给 budget/final 两个位置会让这些材料在时间轴上消失。
    unclassified_slots: List[MaterialSlotSummary]


class UnitTimelineData(BaseModel):
    unit: TimelineUnitRef
    #: 按 ``fiscal_year`` 降序。**只**按 ``material_slots.fiscal_year`` 排，
    #: 绝不按 ``published_at`` / ``uploaded_at`` / ``created_at`` / job 年份
    #: ——2024 年度决算在 2025 年 8 月发布是常态，按发布时间排会把它挪到 2025 行。
    years: List[TimelineYearRow]
    #: ``fiscal_year IS NULL`` 的槽位。它们不进 ``years``，也不兜底成
    #: 2000 / 0 / 当前年份：年份未知是"身份待确认"，不是某个具体年度。
    unresolved_year_slots: List[MaterialSlotSummary]


class UnitTimelineResponse(BaseModel):
    ok: Literal[True] = True
    data: UnitTimelineData
    meta: MaterialDetailMeta


# ---- 版本与来源 --------------------------------------------------------------


class MaterialSourceItem(BaseModel):
    """一条材料来源（``material_sources`` 的一行）。

    ``source_url`` 可以为 null —— 人工上传没有 URL。``source_kind`` 是
    区分"人工上传"与"来源缺失"的唯一依据：``manual_upload`` + 无 URL
    是**正常的**，不是缺数据（§二十六）。
    """

    source_id: str
    source_kind: str
    source_url: Optional[str] = None
    source_page_title: Optional[str] = None
    source_site: Optional[str] = None
    #: 网页发布日期。与 ``slot.fiscal_year`` 严格分开，互不覆盖（§二十五）。
    published_at: Optional[UtcDatetime] = None
    discovered_at: Optional[UtcDatetime] = None
    last_checked_at: Optional[UtcDatetime] = None
    source_page_hash: Optional[str] = None
    status: str


class MaterialVersionItem(BaseModel):
    """一个文件版本（``fiscal_document_versions`` 的一行）。

    **刻意不含 ``storage_key``**：它是内部存储路径 / object key，
    原样返回等于把磁盘布局暴露给浏览器（§四十一）。
    需要预览时走 ``preview_url`` / ``download_url``，两者都经过仓库既有的
    ``require_job_access`` 校验；拿不到安全入口时一律为 null，
    **不制造** ``file://`` 或 ``/uploads/`` 这类绕过鉴权的地址。
    """

    document_version_id: int
    document_id: int
    file_hash: Optional[str] = None
    original_filename: Optional[str] = None
    file_size_bytes: Optional[int] = None
    content_type: Optional[str] = None
    storage_backend: Optional[str] = None
    created_at: Optional[UtcDatetime] = None
    #: 是否等于 ``material_slots.current_document_version_id``。
    #: 真值只有 Slot 上那一个指针，这里只是把它投影成每行一个布尔（§二十二）。
    is_current: bool = False
    #: 与本人权限无关的"能不能预览"：只有该版本存在精确关联的分析运行、
    #: 且本人对该运行有 job 访问权时才给 URL（§七十一）。
    preview_url: Optional[str] = None
    download_url: Optional[str] = None


# ---- 处理记录（分析运行） ----------------------------------------------------


class RunSummaryItem(BaseModel):
    """一次分析运行的摘要。

    **不含** ``raw_response`` / 完整 finding / 完整 evidence（§四十七）：
    处理记录 Tab 一次列出 N 条运行，把 N 份完整分析 JSON 拖到浏览器里
    会让这个 Tab 变成整页最慢的地方。当前分析的详情由
    ``GET /api/materials/slots/{slot_id}`` 按需给。
    """

    job_uuid: str
    #: 精确关联到的文件版本。来自 ``metadata.structured_ingest.document_version_id``，
    #: 不是从文件名猜的。
    document_version_id: int
    is_current_document_version: bool
    status: str
    mode: Optional[str] = None
    started_at: Optional[UtcDatetime] = None
    completed_at: Optional[UtcDatetime] = None
    created_at: Optional[UtcDatetime] = None
    updated_at: Optional[UtcDatetime] = None
    ai_findings_count: Optional[int] = None
    rule_findings_count: Optional[int] = None
    merged_findings_count: Optional[int] = None
    #: 是否已落 ``analysis_results``。false 表示这次运行没有可读的结论。
    has_results: bool = False
    structured_ingest_status: Optional[str] = None
    elapsed_total_ms: Optional[int] = None
    #: 本次运行的错误摘要。**已经过安全过滤**：只保留错误类型/短消息，
    #: 绝不透出堆栈、本地路径或连接串（§五十六）。
    error_summary: Optional[str] = None
    #: 运行结论 / 质量门状态（``analysis_jobs.metadata.result_meta.quality_gate``）。
    #: 旧任务没有这些字段时为 null —— 不假装它是 ``done``。
    analysis_status: Optional[str] = None
    quality_status: Optional[str] = None
    analysis_conclusion: Optional[str] = None
    #: 该运行落库时的检查要求版本（``obligation_coverage.catalog_version``）。
    obligation_catalog_version: Optional[str] = None
    #: 正式问题数：**只有在**"当前版本 → 精确运行 → 已落库结果 → 正式门禁"
    #: 全部成立时才是可算的整数（见 ``AnalysisBlock.formal_issue_count``）。
    #: 运行列表里一律为 null：这里只解释"这次跑了什么"，不下结论。
    formal_issue_count: Optional[int] = None
    #: 精确关联证据：让"为什么这条运行挂在这条材料上"可在界面上自证。
    linkage_basis: Literal["structured_document_version_id"] = SLOT_LINKAGE_BASIS


class SlotRunListData(BaseModel):
    slot_id: str
    #: 按运行的有效完成时刻降序（仓库既有排序，见查询服务说明）。
    items: List[RunSummaryItem]


class SlotRunListResponse(BaseModel):
    ok: Literal[True] = True
    data: SlotRunListData
    meta: MaterialDetailMeta


class SlotVersionListData(BaseModel):
    slot_id: str
    #: 按 ``created_at DESC, document_version_id DESC`` 稳定排序。
    items: List[MaterialVersionItem]
    current_document_version_id: Optional[int] = None


class SlotVersionListResponse(BaseModel):
    ok: Literal[True] = True
    data: SlotVersionListData
    meta: MaterialDetailMeta


# ---- 当前分析：检查结果 ------------------------------------------------------


class AnalysisFindingItem(BaseModel):
    """一条 finding 的展示投影。

    只带页面需要的字段：标题、严重度、来源、规则/义务编号、证据位置、说明。
    完整 finding（含全部 evidence 明细）留在 ``/api/jobs/{id}`` 里，
    这个接口不做第二个原文出口。
    """

    finding_id: Optional[str] = None
    title: Optional[str] = None
    message: Optional[str] = None
    severity: Optional[str] = None
    #: 严重度的归并桶（``error`` / ``warn`` / ``info``）。与仓库既有
    #: ``pipeline._norm_sev`` 同一口径，供前端排序与配色使用；
    #: 原始 ``severity`` 同时保留，不做有损替换。
    severity_bucket: Literal["error", "warn", "info"]
    source: Optional[str] = None
    rule_id: Optional[str] = None
    obligation_ids: List[str] = Field(default_factory=list)
    evidence_page: Optional[int] = None
    evidence_bbox: Optional[List[float]] = None
    evidence_text: Optional[str] = None
    #: 证据链状态（``evidence_guard`` 的三个真实取值：``complete`` /
    #: ``degraded_missing_evidence`` / ``incomplete_rule_warning``）。
    #: ``degraded_missing_evidence`` 即"缺证据被降级"，这类条目进
    #: "需人工核验"而不是"正式问题"。
    evidence_status: Optional[str] = None
    evidence_missing: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    why_not: Optional[str] = None


# ---- 当前分析：检查覆盖 ------------------------------------------------------


class CoverageGroupItem(BaseModel):
    """义务分组的一行（对应效果图「检查义务台账」的每个分组）。"""

    group_id: str
    #: 分组中文名由**引擎**产出并已落库（``OBLIGATION_GROUP_TITLES``），
    #: 这里原样透出，不在 API 层新造第二套中文。
    group_title: Optional[str] = None
    applicable: int
    completed: int
    not_applicable: int
    unresolved: int


class CoverageObligationItem(BaseModel):
    """单条检查义务的展开明细。

    ``obligation_id`` 是技术编号，界面把它放进"展开详情"而不是主文案（§三十六）。
    ``status`` / ``reason`` 都是技术码，中文在展示层。
    """

    obligation_id: str
    group_id: Optional[str] = None
    group_title: Optional[str] = None
    title: Optional[str] = None
    status: str
    reason: Optional[str] = None
    #: 引擎给出的原因中文（已落库），与 ``reason`` 并存：
    #: 前者供人读，后者供机器判定，二者都不是本层新造的。
    reason_label: Optional[str] = None
    detail: Optional[str] = None
    blocks_gate: bool = False
    requires_ai: bool = False
    input_gaps: List[str] = Field(default_factory=list)


class CoverageSummaryBlock(BaseModel):
    """检查覆盖的汇总数字。

    ``applicable_total == 0`` 时 ``coverage_rate`` 为 ``None``：
    "没有应检查事项"不等于"检查完整"，0 分母不许算成 100%。
    """

    catalog_version: Optional[str] = None
    catalog_fingerprint: Optional[str] = None
    applicable_total: int
    completed_total: int
    not_applicable_total: int
    unresolved_total: int
    #: **阻塞**未完成数（存在未实现 / 未执行 / 取数不足 / 解析歧义 / 文种冲突）。
    #: 它 > 0 时不得把材料标成"检查完成"。
    blocking_total: int
    coverage_rate: Optional[float] = None
    auto_completion_rate: Optional[float] = None
    #: 未完成原因码 → 条数（技术码，中文在展示层）。
    by_reason: Dict[str, int] = Field(default_factory=dict)
    by_group: List[CoverageGroupItem] = Field(default_factory=list)


class CoverageBlock(BaseModel):
    """检查覆盖块。

    ``available=false`` 时必须整体为空：``summary=None``、``items=[]``。
    页面显示"当前文件版本暂无可确认的检查覆盖记录"，
    **禁止**显示 ``0 / 0``、``100%`` 或"全部通过"（§三十七）。
    """

    available: bool = False
    reason: Optional[str] = None
    summary: Optional[CoverageSummaryBlock] = None
    items: List[CoverageObligationItem] = Field(default_factory=list)


class AnalysisBlock(BaseModel):
    """当前文件版本的分析结果。

    可用性判定链（缺一环就整体不可用，§二十八/§三十）：
    ``slot.current_document_version_id`` → 精确匹配该版本的 analysis job
    → 该 job 已落 ``analysis_results`` → 正式门禁可算。
    "这条槽位最近一次分析"**不是**替代品：版本替换后它会显示上一版的结论。
    """

    available: bool = False
    reason: Optional[str] = None
    run: Optional[RunSummaryItem] = None
    #: 正式问题（通过 ``is_formal_finding`` 门禁、且严重度不是 info）。
    formal_findings: Optional[List[AnalysisFindingItem]] = None
    #: 需人工核验：缺证据被降级为 ``manual_review`` 的条目。**不是**正式问题。
    manual_review_items: Optional[List[AnalysisFindingItem]] = None
    #: 信息提示：通过正式门禁但严重度为 info 的条目。
    info_findings: Optional[List[AnalysisFindingItem]] = None
    #: 正式问题数。``None`` = 现在算不出来；``0`` = 算过且确实没有正式问题。
    #: 只有上面那条链路全部成立时才允许出现 0（§三十三）。
    formal_issue_count: Optional[int] = None
    coverage: Optional[CoverageBlock] = None


# ---- 接口五：Slot Detail -----------------------------------------------------


class SlotDetailData(BaseModel):
    """:``GET /api/materials/slots/{slot_id}`` 的 data。

    ``slot`` 复用 WP2-A 的 ``MaterialSlotSummary``：同一件事只有一套字段名。
    """

    slot: MaterialSlotSummary
    #: 当前文件版本。**唯一真值**是 ``slot.current_document_version_id``；
    #: 为 NULL 时这里是 null，**不**从历史版本里挑最新的一份冒充（§二十二/§二十三）。
    current_version: Optional[MaterialVersionItem] = None
    #: 历史版本数量（不含当前版本）。用于 missing 态文案区分
    #: "当前无有效文件版本"与"该槽位尚未关联任何 PDF 版本"（§五十八）。
    historical_version_count: int = 0
    version_total: int = 0
    sources: List[MaterialSourceItem] = Field(default_factory=list)
    current_analysis: AnalysisBlock = Field(default_factory=AnalysisBlock)


class SlotDetailResponse(BaseModel):
    ok: Literal[True] = True
    data: SlotDetailData
    meta: MaterialDetailMeta


__all__ = [
    "SLOT_LINKAGE_BASIS",
    "LEGACY_UNLINKED_RUNS_EXCLUDED",
    "COVERAGE_REASON_NONE_FOR_CURRENT_VERSION",
    "ANALYSIS_REASON_NONE_FOR_CURRENT_VERSION",
    "ANALYSIS_REASON_NO_CURRENT_VERSION",
    "DATA_BASIS_EXISTING_SLOTS_ONLY",
    "EXPECTED_MATERIALS_READY",
    "MaterialDetailMeta",
    "build_detail_meta",
    "TimelineUnitRef",
    "TimelineYearRow",
    "UnitTimelineData",
    "UnitTimelineResponse",
    "MaterialSourceItem",
    "MaterialVersionItem",
    "RunSummaryItem",
    "SlotRunListData",
    "SlotRunListResponse",
    "SlotVersionListData",
    "SlotVersionListResponse",
    "AnalysisFindingItem",
    "CoverageGroupItem",
    "CoverageObligationItem",
    "CoverageSummaryBlock",
    "CoverageBlock",
    "AnalysisBlock",
    "SlotDetailData",
    "SlotDetailResponse",
]
