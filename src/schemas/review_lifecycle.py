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

from pydantic import BaseModel, Field

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


class ReviewLifecycleData(BaseModel):
    """``GET /api/reviews*`` 的统一响应体。"""

    slot_id: str
    current_document_version_id: Optional[int] = None
    current_analysis: CurrentAnalysisRef
    current_session: Optional[ReviewSessionSummary] = None
    history: List[ReviewHistoryItem] = Field(default_factory=list)
    completion_gate: CompletionGate


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
