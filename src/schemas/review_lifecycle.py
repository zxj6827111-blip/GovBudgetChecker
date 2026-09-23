"""人工复核生命周期（Review Lifecycle，WP3-A）的统一契约。

为什么必须是一个独立业务对象
----------------------------
``review_session`` **不等于** ``issue_workflow record``，也**不等于** ``analysis job``：

    一条材料可以  V1 分析 → 复核完成
                 V2 分析 → 再次复核

``issue_workflow`` 只回答"这一条问题被怎么处理了"；``analysis job`` 只回答
"跑过几次分析"。两者都回答不了"**哪一版文件、哪一代分析**上的复核结论"。
把复核结论挂在问题记录上，等于让"这份材料复核完成了吗"这个事实随问题条数变化而漂移。

四条不可让步的口径
------------------
1. **复核必须绑四元组。** ``slot_id + document_version_id + analysis_job_uuid +
   analysis_basis_token``，缺一不可。只绑 ``job_uuid`` 是不够的：
   ``_upsert_analysis_job`` / ``_upsert_analysis_result`` 都是 ``ON CONFLICT DO UPDATE``，
   **同一个 job_uuid 重新分析会原地覆盖上面的分析结果行**。因此
   "同 job_uuid + 同版本 + 重新分析"必须让 ``analysis_basis_token`` 变化，
   否则旧复核会错误继承新结论。
2. **失效是保留，不是删除。** 历史必须可追溯（V1 completed、V2 invalidated、
   V2 completed 都要留着）。禁止"开始新复核 → 删掉旧复核"。
3. **未知不得当成零。** 取不到检查覆盖（``coverage.available = false``）时
   ``can_complete = false`` + ``coverage_unavailable``；把"不知道"读成
   "0 条阻塞义务"正是本项目一直在修的那类假完成。
4. **完成只能由服务端判定。** 前端按钮的 disabled 只是即时提示；
   真正的完成门禁在服务端重新计算全部条件，前端传什么都不作数。

中文文案不在本模块
------------------
阻塞码（``blocker.code``）与原因码（``invalidated_reason``）原样输出，
中文映射在展示层（``app/lib/reviewLifecyclePresentation.ts``）。与 WP2 的纪律一致：
后端翻译会让同一原因在两端各写一份，必然漂移。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

# ---- 会话状态 --------------------------------------------------------------

REVIEW_STATUS_IN_PROGRESS = "in_progress"
REVIEW_STATUS_COMPLETED = "completed"
REVIEW_STATUS_INVALIDATED = "invalidated"

REVIEW_STATUSES: tuple = (
    REVIEW_STATUS_IN_PROGRESS,
    REVIEW_STATUS_COMPLETED,
    REVIEW_STATUS_INVALIDATED,
)

# ---- 失效原因（机器可读） --------------------------------------------------

INVALIDATION_REASON_DOCUMENT_VERSION_CHANGED = "document_version_changed"
INVALIDATION_REASON_ANALYSIS_RESTARTED = "analysis_restarted"
INVALIDATION_REASON_ANALYSIS_BASIS_CHANGED = "analysis_basis_changed"
INVALIDATION_REASON_REVIEW_REOPENED = "review_reopened"

# ---- 完成门禁阻塞码（机器可读） --------------------------------------------

BLOCKER_DOCUMENT_VERSION_CHANGED = "document_version_changed"
BLOCKER_ANALYSIS_BASIS_CHANGED = "analysis_basis_changed"
BLOCKER_IDENTITY_UNRESOLVED = "identity_unresolved"
BLOCKER_CALIBER_CONFLICT = "caliber_conflict"

BLOCKER_PENDING_FINDINGS = "pending_findings"
BLOCKER_NEEDS_REVIEW_FINDINGS = "needs_review_findings"

BLOCKER_COVERAGE_UNAVAILABLE = "coverage_unavailable"
BLOCKER_BLOCKING_OBLIGATIONS = "blocking_obligations"

BLOCKER_ANALYSIS_UNAVAILABLE = "analysis_unavailable"
BLOCKER_ANALYSIS_NOT_COMPLETED = "analysis_not_completed"

#: 开复核的门禁阻塞码（与完成门禁共用取值域，但只用到其中一部分）。
BLOCKER_NO_CURRENT_VERSION = "no_current_document_version"
BLOCKER_REVIEW_ALREADY_COMPLETED = "review_already_completed"

#: 完成门禁的额外阻塞码：还没有开始复核。
#: §四十九 要求的是"至少包含"以下阻塞码，这是本仓补充的一条。
#: 它的存在是一条硬纪律的落点：**"没有问题"不等于"人已经确认过"**——
#: 即使 0 条问题、0 条阻塞义务，也必须先有人显式开始并显式完成复核（§八十二）。
BLOCKER_REVIEW_NOT_STARTED = "review_not_started"

#: 业务错误码（HTTP 409 + ``detail.error``）。
ERROR_COMPLETION_BLOCKED = "review_completion_blocked"
ERROR_START_BLOCKED = "review_start_blocked"
ERROR_REVIEW_CONTEXT_UNAVAILABLE = "review_context_unavailable"
ERROR_REVIEW_CONTEXT_MISMATCH = "review_context_mismatch"
ERROR_REVIEW_COMPLETED_LOCKED = "review_completed_locked"

#: 编辑锁（文件存储）写不进去。复核完成是**安全约束**，锁没落下就不允许宣称完成，
#: 因此这条错误会让本次复核失败并回滚数据库事务（503：存储暂时不可用）。
ERROR_REVIEW_LOCK_UNAVAILABLE = "review_lock_unavailable"

#: 完成门禁判定所用的"问题集合快照"在落编辑锁之前发生了变化（issue 被改状态、
#: 或被加入忽略清单）。fail-closed：不写锁、回滚、请用户刷新后重新确认。
ERROR_REVIEW_WORKFLOW_CHANGED = "review_workflow_changed"

# ---- 人工补核决定（WP3-B） ---------------------------------------------------
#
# 词汇口径（先查仓库既有命名，再定新词）：
#
# - ``pending`` 与 issue 域（``ISSUE_STATUSES``）同词同义：没有任何人表过态。
#   数据库里**没有这一行**就是 pending——不存的"待处理"与存下来的"待处理"
#   对门禁完全一致，避免"有没有记录"改变语义。
# - ``not_applicable`` 与引擎义务状态（``check_obligations.OBLIGATION_NOT_APPLICABLE``）
#   同词同义：不适用。区别只在判定者——这里是**人工**判定不适用，引擎那边是
#   规则/画像判定不适用。两个命名空间不混用，但措辞不另造。
# - ``verified_ok`` / ``verified_issue`` 是补核动作的两个结论：人工核对后确认
#   "该项检查义务实际已履行、无问题"，或"确认这里确实存在问题（已知晓/已记录）"。
#   不借用 issue 域的 ``confirmed`` / ``no_issue``：义务不是问题，
#   "确认一条义务"与"确认一个问题"是两个业务动作，用词分开才能让审计一眼区分。
#
# 禁止自动关闭（任务书红线）：``verified_ok`` / ``verified_issue`` /
# ``not_applicable`` 只能由人工显式写入；任何"覆盖不可用→不适用""
# 取数不足→没问题""AI 通过→没问题"的自动推导都不允许存在。
OBLIGATION_DECISION_PENDING = "pending"
OBLIGATION_DECISION_VERIFIED_OK = "verified_ok"
OBLIGATION_DECISION_VERIFIED_ISSUE = "verified_issue"
OBLIGATION_DECISION_NOT_APPLICABLE = "not_applicable"

OBLIGATION_DECISIONS: tuple = (
    OBLIGATION_DECISION_PENDING,
    OBLIGATION_DECISION_VERIFIED_OK,
    OBLIGATION_DECISION_VERIFIED_ISSUE,
    OBLIGATION_DECISION_NOT_APPLICABLE,
)

#: 视为"已处理"的决定。之外的取值（``pending``、**没有记录**、任何未知值）
#: 全部阻塞完成门禁——与 ``_problem_blockers`` 同一条纪律：把不认识的值当成
#: "已解决"等于让一个拼错的状态码静默放行复核。
RESOLVED_OBLIGATION_DECISIONS: frozenset = frozenset(
    {
        OBLIGATION_DECISION_VERIFIED_OK,
        OBLIGATION_DECISION_VERIFIED_ISSUE,
        OBLIGATION_DECISION_NOT_APPLICABLE,
    }
)

#: 业务错误码（HTTP 409 + ``detail.error``），沿用 ``review_*`` 前缀风格。
#: 没有活动会话时不能补核：决定必须挂在"当前这一次复核"上，先开始再处理。
ERROR_REVIEW_NOT_ACTIVE = "review_not_active"
#: 目标义务当前不由引擎判定为阻塞未完成（可能已自动完成或不适用）——
#: 它没有东西需要人工表态。拒绝写入，避免"给一条已完成义务补核"这种
#: 自己和自己打架的记录。
ERROR_OBLIGATION_NOT_BLOCKING = "obligation_not_blocking"
#: 并发/过期写入被拒：首次创建撞了唯一约束，或更新时 ``expected_revision``
#: 与库里的 ``revision`` 不一致。客户端必须刷新后重试——**不允许**静默覆盖
#: 另一个复核人的决定。
ERROR_OBLIGATION_DECISION_CONFLICT = "obligation_decision_conflict"


class ReviewBlocker(BaseModel):
    """一条完成门禁阻塞。``count`` 只对计数类阻塞有意义。"""

    code: str = Field(description="机器可读阻塞码")
    count: Optional[int] = Field(default=None, description="计数类阻塞的条数；非计数类为 null")


class CompletionGate(BaseModel):
    """完成门禁结论。服务端重算，前端不得自行推导。"""

    can_complete: bool
    blockers: List[ReviewBlocker] = Field(default_factory=list)


class CurrentAnalysisRef(BaseModel):
    """当前分析（某一份文件版本上的当前一次运行）。"""

    job_uuid: Optional[str] = None
    analysis_revision: Optional[int] = None
    analysis_basis_token: Optional[str] = None
    document_version_id: Optional[int] = None
    status: Optional[str] = Field(default=None, description="analysis_jobs.status 原值")
    completed: bool = Field(default=False, description="这次运行是否已产出落库的分析结果")
    formal_issue_count: Optional[int] = Field(
        default=None,
        description="正式问题数；None 表示算不出来（与 0 = 确实没有 严格区分）",
    )


class ReviewSessionSummary(BaseModel):
    """复核会话的对外形态。"""

    review_session_id: str
    status: str
    slot_id: str
    document_version_id: int

    analysis_job_uuid: str
    analysis_basis_token: str

    started_by: str
    started_at: str

    completed_by: Optional[str] = None
    completed_at: Optional[str] = None

    invalidated_at: Optional[str] = None
    invalidated_reason: Optional[str] = None

    review_result: Dict = Field(default_factory=dict)


class ReviewHistoryItem(BaseModel):
    """历史会话（已终结的）。"""

    review_session_id: str
    status: str
    document_version_id: int
    analysis_job_uuid: str
    analysis_basis_token: str

    started_by: str
    started_at: str
    completed_by: Optional[str] = None
    completed_at: Optional[str] = None
    invalidated_at: Optional[str] = None
    invalidated_reason: Optional[str] = None


class ReviewContextData(BaseModel):
    """``GET /api/reviews``（按 job_uuid 解析）返回的上下文可用性。

    没有可持久化复核上下文的旧任务不是错误状态，而是**正常但不可复核**的
    历史任务：页面照样能看旧审核内容，只是不能点"完成复核"（§七十三）。
    因此这里用 200 + ``available=false``，而不是 409。
    """

    available: bool
    reason: Optional[str] = None
    slot_id: Optional[str] = None
    job_uuid: Optional[str] = None


# ---- 人工补核（WP3-B）：obligation 级人工决定 --------------------------------
#
# 数据归属（任务书红线）：每条决定**必须**绑定 ``review_session_id``——
# 同一份材料可以 V1 复核、V2 复核、多个分析代际，"obligation_id + job_uuid"
# 的关联方式无法区分"这是哪一次复核上的补核结论"。``obligation_id`` 只在
# 会话内有意义（它指向该会话钉住的分析代际上的义务账本实例）。


class ObligationDecisionRecord(BaseModel):
    """一条人工补核决定的对外形态（对应表 ``review_obligation_decisions``）。"""

    review_session_id: str
    slot_id: str
    obligation_id: str
    decision: str = Field(description="pending / verified_ok / verified_issue / not_applicable")
    note: Optional[str] = None
    evidence_reference: Optional[str] = None
    reviewer: str
    reviewed_at: str
    #: 乐观锁代数：每次写入 +1。更新已有决定必须带上它，带错一律 409
    #: （``obligation_decision_conflict``）——不允许静默覆盖别人的决定。
    revision: int


class ObligationReviewItem(BaseModel):
    """复核视角的一条检查义务：引擎判定的未完成实例 + 当前会话的人工决定。

    ``decision=None`` 就是"待处理"（数据库里没有这一行），与显式存一条
    ``pending`` 语义完全一致——门禁只关心"有没有已解决决定"，不关心
    待处理是哪种写法。
    """

    obligation_id: str
    group_id: Optional[str] = None
    group_title: Optional[str] = None
    title: Optional[str] = None
    #: 引擎判定状态（not_executed / insufficient_data / …），机器码，中文在展示层。
    status: str
    reason: Optional[str] = None
    reason_label: Optional[str] = None
    detail: Optional[str] = None
    #: 当前有效会话上的人工决定；None = 尚无记录（待处理）。
    decision: Optional[str] = None
    decision_revision: Optional[int] = None
    decided_by: Optional[str] = None
    decided_at: Optional[str] = None
    note: Optional[str] = None
    evidence_reference: Optional[str] = None


class ObligationReviewBlock(BaseModel):
    """``GET /api/reviews*`` 里的人工补核块。

    只包含**当前会话需要人工处理**的义务（引擎判阻塞 ∧ 未完成），并叠加上
    当前有效会话已经写下的决定。已自动完成的义务在材料详情的覆盖页展示，
    不属于这里的待办清单。

    ``available=false`` 时 ``items=[]`` 且 ``pending_total=None``：
    "覆盖不可用"与"0 项待补核"在数字上不能长得一样（与 CoverageBlock 同一纪律）。
    """

    available: bool = False
    reason: Optional[str] = None
    pending_total: Optional[int] = Field(
        default=None, description="尚无已解决决定的阻塞义务条数；覆盖不可用时为 null"
    )
    items: List[ObligationReviewItem] = Field(default_factory=list)


class ObligationDecisionRequest(BaseModel):
    """``PUT /api/reviews/{slot_id}/obligations/{obligation_id}`` 的请求体。

    - ``decision`` 只允许四个取值，违例 422（参数问题）；
    - 首次表态不传 ``expected_revision``；改写已有决定必须带上读到的
      ``revision``，带错/缺省一律 409——**禁止**在不知情时覆盖他人的决定
      （并发红线，任务书 § 十五）；
    - ``job_uuid`` 与 ``ReviewActionRequest`` 的口径一致：只被当作"待核对的声明"，
      服务端自行验证它属于这条槽位且正是当前分析。
    """

    decision: str
    note: Optional[str] = Field(default=None, max_length=500)
    evidence_reference: Optional[str] = Field(default=None, max_length=200)
    expected_revision: Optional[int] = Field(default=None, ge=1)
    job_uuid: Optional[str] = None

    @field_validator("decision")
    @classmethod
    def _decision_must_be_known(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if normalized not in OBLIGATION_DECISIONS:
            raise ValueError(
                "decision must be one of: " + ", ".join(OBLIGATION_DECISIONS)
            )
        return normalized


class ObligationDecisionData(BaseModel):
    """补核写入的响应体：决定本身 + 重算后的门禁（与 ``ReviewMutationData`` 同构）。"""

    slot_id: str
    current_document_version_id: Optional[int] = None
    session: ReviewSessionSummary
    decision: ObligationDecisionRecord
    completion_gate: CompletionGate
    blockers: List[ReviewBlocker] = Field(default_factory=list)


class ObligationDecisionResponse(BaseModel):
    ok: bool = True
    data: ObligationDecisionData


class ReviewLifecycleData(BaseModel):
    """``GET /api/reviews*`` 的统一响应体。"""

    slot_id: str
    current_document_version_id: Optional[int] = None
    current_analysis: CurrentAnalysisRef
    current_session: Optional[ReviewSessionSummary] = None
    history: List[ReviewHistoryItem] = Field(default_factory=list)
    completion_gate: CompletionGate
    #: 人工补核块（WP3-B）。注意它**不是**覆盖台账的替代品：材料详情页的
    #: CoverageTab 展示全量义务，这里只给"本次复核要人工处理的待办"。
    obligation_review: ObligationReviewBlock = Field(default_factory=ObligationReviewBlock)


class ReviewByJobData(BaseModel):
    """按 job_uuid 解析时的响应体：上下文不可用时也要能安全渲染。"""

    review_context: ReviewContextData
    review: Optional[ReviewLifecycleData] = None


class ReviewLifecycleResponse(BaseModel):
    ok: bool = True
    data: ReviewLifecycleData


class ReviewByJobResponse(BaseModel):
    ok: bool = True
    data: ReviewByJobData


class ReviewMutationData(BaseModel):
    """start / complete / reopen 的响应体：会话 + 重算后的门禁。"""

    slot_id: str
    current_document_version_id: Optional[int] = None
    session: ReviewSessionSummary
    completion_gate: CompletionGate
    blockers: List[ReviewBlocker] = Field(default_factory=list)


class ReviewActionRequest(BaseModel):
    """start / complete / reopen 的请求体。

    只允许带 ``job_uuid`` 一个字段。**前端传什么都不改变判定**：服务端会用
    精确链路自行验证这个 job 确实属于 URL 里的槽位、且正是当前分析，
    不匹配一律 409（§二十八）。这个字段的作用是让"用户看的是哪一次运行"
    这件事可被服务端核对，而不是让前端替服务端决定复核对象。
    """

    job_uuid: Optional[str] = None


class ReviewMutationResponse(BaseModel):
    ok: bool = True
    data: ReviewMutationData


# ---- 复核问题集合（与审核工作台同源的口径） --------------------------------

#: 问题来源。前端 ``toUiProblems`` 综合的四类来源在这里被显式命名，
#: 避免"前端一套、后端一套"各说各话。
PROBLEM_SOURCE_FINDING = "finding"
PROBLEM_SOURCE_STRUCTURED_REVIEW = "structured_ingest"

#: 视为"已处理"的问题状态。三者之外（``pending`` / ``needs_review``）全部阻塞完成。
RESOLVED_ISSUE_STATUSES: frozenset = frozenset({"confirmed", "no_issue", "in_package"})

#: 阻塞完成的问题状态。
BLOCKING_ISSUE_STATUSES: frozenset = frozenset({"pending", "needs_review"})

#: 问题状态取值域（与 ``issue_workflow_store._VALID_STATUSES`` 同源）。
ISSUE_STATUSES: tuple = ("pending", "confirmed", "no_issue", "needs_review", "in_package")


__all__ = [
    "REVIEW_STATUS_IN_PROGRESS",
    "REVIEW_STATUS_COMPLETED",
    "REVIEW_STATUS_INVALIDATED",
    "REVIEW_STATUSES",
    "INVALIDATION_REASON_DOCUMENT_VERSION_CHANGED",
    "INVALIDATION_REASON_ANALYSIS_RESTARTED",
    "INVALIDATION_REASON_ANALYSIS_BASIS_CHANGED",
    "INVALIDATION_REASON_REVIEW_REOPENED",
    "BLOCKER_DOCUMENT_VERSION_CHANGED",
    "BLOCKER_ANALYSIS_BASIS_CHANGED",
    "BLOCKER_IDENTITY_UNRESOLVED",
    "BLOCKER_CALIBER_CONFLICT",
    "BLOCKER_PENDING_FINDINGS",
    "BLOCKER_NEEDS_REVIEW_FINDINGS",
    "BLOCKER_COVERAGE_UNAVAILABLE",
    "BLOCKER_BLOCKING_OBLIGATIONS",
    "BLOCKER_ANALYSIS_UNAVAILABLE",
    "BLOCKER_ANALYSIS_NOT_COMPLETED",
    "BLOCKER_NO_CURRENT_VERSION",
    "BLOCKER_REVIEW_ALREADY_COMPLETED",
    "BLOCKER_REVIEW_NOT_STARTED",
    "ERROR_COMPLETION_BLOCKED",
    "ERROR_START_BLOCKED",
    "ERROR_REVIEW_CONTEXT_UNAVAILABLE",
    "ERROR_REVIEW_CONTEXT_MISMATCH",
    "ERROR_REVIEW_COMPLETED_LOCKED",
    "ERROR_REVIEW_LOCK_UNAVAILABLE",
    "ERROR_REVIEW_WORKFLOW_CHANGED",
    "OBLIGATION_DECISION_PENDING",
    "OBLIGATION_DECISION_VERIFIED_OK",
    "OBLIGATION_DECISION_VERIFIED_ISSUE",
    "OBLIGATION_DECISION_NOT_APPLICABLE",
    "OBLIGATION_DECISIONS",
    "RESOLVED_OBLIGATION_DECISIONS",
    "ERROR_REVIEW_NOT_ACTIVE",
    "ERROR_OBLIGATION_NOT_BLOCKING",
    "ERROR_OBLIGATION_DECISION_CONFLICT",
    "ObligationDecisionRecord",
    "ObligationReviewItem",
    "ObligationReviewBlock",
    "ObligationDecisionRequest",
    "ObligationDecisionData",
    "ObligationDecisionResponse",
    "ReviewBlocker",
    "CompletionGate",
    "CurrentAnalysisRef",
    "ReviewSessionSummary",
    "ReviewHistoryItem",
    "ReviewContextData",
    "ReviewLifecycleData",
    "ReviewByJobData",
    "ReviewLifecycleResponse",
    "ReviewByJobResponse",
    "ReviewMutationData",
    "ReviewMutationResponse",
    "ReviewActionRequest",
    "PROBLEM_SOURCE_FINDING",
    "PROBLEM_SOURCE_STRUCTURED_REVIEW",
    "RESOLVED_ISSUE_STATUSES",
    "BLOCKING_ISSUE_STATUSES",
    "ISSUE_STATUSES",
]
