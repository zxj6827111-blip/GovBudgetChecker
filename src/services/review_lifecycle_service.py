"""复核生命周期：把"完成复核"从一次前端跳转变成可持久化的服务端事实。

问题是什么
----------
整改前，审核工作台的「完成复核」只在 ``pending == 0`` 时 ``router.push("/queue")``
——**没有任何后端写入**。刷新页面、重启服务之后，系统并不知道这份材料被复核过。
于是"复核完成"是一个只存在于那一刻的浏览器里的结论。

本模块提供唯一的状态变化入口（start / complete / invalidate / reopen / gate），
不允许把状态写入散落在多个路由里：状态机一旦有两处实现，就一定出现两个答案。

三层门禁
--------
1. **开复核门禁**：槽位有当前版本、该版本有可读的当前分析、身份与口径没有悬而未决；
2. **完成门禁**：在 (1) 的基础上，还要"版本没换""分析代际没变""问题都有人表过态"
   "检查覆盖可信且没有阻塞义务"；
3. **失效钩子**：版本指针推进、重新分析这两条路径主动让复核失效，
   而不是等下次打开页面才发现（"下次打开才发现"期间，页面会理直气壮地显示
   「已完成复核」，而它复核的已经不是当前这一版 / 这一代结果）。

双保险
------
每个读/写路径都会重新对照当前事实与会话上钉住的事实：

- ``GET`` / ``start``：发现活动会话钉的版本或代际与当前不符 → 就地失效并记审计；
- ``complete``：任何一项不符 → 409 + ``blockers``，**绝不**把结论写成完成；
- 失效钩子：在写入那一侧主动失效，让界面能立刻显示"需要重新复核"。

只有一层是不够的：懒失效保证正确性（最终一定拦得住），主动钩子保证可观测性
（用户不必等到重新打开页面才知道要重做）；两层都做，缺一层都会留下
"看起来正常但其实已经过期"的窗口。

LOCK ORDER（在 ``review_session_store`` 的全序之内）
---------------------------------------------------
    身份 advisory 锁 → material_slots → fiscal_document_versions
        → review_sessions → review_obligation_decisions（WP3-B）→ analysis_jobs

本模块严格遵守：先 ``lock_slot_row``（槽位行锁），再取活动会话行锁，
**在拿到这两把锁之后**才去读"当前分析代际"。顺序反过来（先读代际再加锁）
会留下一个窗口：重新分析在"读完代际"之后、"取会话锁"之前提交，
本次复核就把旧代际记成完成。相关的并发用例（complete 与重新分析竞争）
在真库上逐条验证。

补核决定（WP3-B）排在会话之后：它只在"槽位锁 + 会话锁"都已持有的事业内
写入，与完成判定天然同一把锁、同一个一致快照——补核与完成不可能出现
"写进去了但判定没看到"或反过来的窗口。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from src.db.transaction import in_transaction

from src.engine.check_obligations import UNRESOLVED_OBLIGATION_STATUSES
from src.schemas.material_detail import CoverageObligationItem
from src.schemas.material_slot import slot_identity_is_resolved
from src.schemas.review_lifecycle import (
    BLOCKER_ANALYSIS_BASIS_CHANGED,
    BLOCKER_ANALYSIS_NOT_COMPLETED,
    BLOCKER_ANALYSIS_UNAVAILABLE,
    BLOCKER_BLOCKING_OBLIGATIONS,
    BLOCKER_CALIBER_CONFLICT,
    BLOCKER_COVERAGE_UNAVAILABLE,
    BLOCKER_DOCUMENT_VERSION_CHANGED,
    BLOCKER_IDENTITY_UNRESOLVED,
    BLOCKER_NEEDS_REVIEW_FINDINGS,
    BLOCKER_NO_CURRENT_VERSION,
    BLOCKER_PENDING_FINDINGS,
    BLOCKER_REVIEW_ALREADY_COMPLETED,
    BLOCKER_REVIEW_NOT_STARTED,
    ERROR_COMPLETION_BLOCKED,
    ERROR_OBLIGATION_DECISION_CONFLICT,
    ERROR_OBLIGATION_DECISION_EVIDENCE_REQUIRED,
    ERROR_OBLIGATION_NOT_BLOCKING,
    ERROR_REVIEW_COMPLETED_LOCKED,
    ERROR_REVIEW_CONTEXT_MISMATCH,
    ERROR_REVIEW_CONTEXT_UNAVAILABLE,
    ERROR_REVIEW_LOCK_UNAVAILABLE,
    ERROR_REVIEW_NOT_ACTIVE,
    ERROR_REVIEW_WORKFLOW_CHANGED,
    ERROR_START_BLOCKED,
    INVALIDATION_REASON_ANALYSIS_BASIS_CHANGED,
    INVALIDATION_REASON_ANALYSIS_RESTARTED,
    INVALIDATION_REASON_DOCUMENT_VERSION_CHANGED,
    INVALIDATION_REASON_REVIEW_REOPENED,
    OBLIGATION_DECISION_NOT_APPLICABLE,
    OBLIGATION_DECISION_PENDING,
    OBLIGATION_DECISION_VERIFIED_ISSUE,
    OBLIGATION_DECISION_VERIFIED_OK,
    OBLIGATION_DECISIONS,
    RESOLVED_OBLIGATION_DECISIONS,
    REVIEW_STATUS_COMPLETED,
    RESOLVED_ISSUE_STATUSES,
    CompletionGate,
    CurrentAnalysisRef,
    ObligationDecisionData,
    ObligationDecisionRecord,
    ObligationReviewBlock,
    ObligationReviewItem,
    ReviewBlocker,
    ReviewByJobData,
    ReviewContextData,
    ReviewHistoryItem,
    ReviewLifecycleData,
    ReviewMutationData,
    ReviewSessionSummary,
)
from src.services import review_context_query, review_obligation_store, review_session_store
from src.services.audit_log import append_audit_event
from src.services.material_detail_query_service import build_coverage
from src.services.material_slot_service import MaterialSlotService
from src.services.material_slot_status import (
    ANALYSIS_DONE,
    REVIEW_COMPLETED,
    REVIEW_IN_PROGRESS,
    REVIEW_REQUIRED,
)
from src.services.review_problem_set import (
    ReviewProblem,
    build_issue_counts,
    collect_review_problems,
)

logger = logging.getLogger(__name__)


class ReviewLifecycleError(HTTPException):
    """复核生命周期的业务错误。

    沿用仓库既有的 FastAPI ``detail`` 契约（§一百零五），但 ``detail`` 是一个
    **对象**而不是字符串：前端要按 ``error`` 分派、按 ``blockers`` 逐条渲染业务原因。
    只回一句 ``HTTP 409`` 等于把"为什么不能完成"这个唯一有价值的信息丢掉。

    状态码语义（§一百零六）：403 无权、404 不存在、409 有权限但业务状态不允许、
    422 参数、503 数据库不可用。本类只用于 409 与 403 这两类业务拒绝。
    """

    def __init__(
        self,
        status_code: int,
        error: str,
        message: str,
        *,
        blockers: Optional[List[ReviewBlocker]] = None,
        **extra: Any,
    ) -> None:
        detail: Dict[str, Any] = {"error": error, "message": message}
        if blockers:
            detail["blockers"] = [item.model_dump() for item in blockers]
        for key, value in extra.items():
            if value is not None:
                detail[key] = value
        super().__init__(status_code=status_code, detail=detail)
        self.error = error
        self.blockers = list(blockers or [])


@dataclass
class ReviewState:
    """一次门禁判定所需的全部事实。**只在同一事务、同一批锁之内构造。**"""

    slot_row: Dict[str, Any]
    slot_id: str
    analysis: review_context_query.ReviewAnalysis
    basis_token: Optional[str]
    active_session: Optional[Dict[str, Any]] = None
    completed_session: Optional[Dict[str, Any]] = None
    history: List[Dict[str, Any]] = field(default_factory=list)
    problems: List[ReviewProblem] = field(default_factory=list)
    decisions: Dict[str, str] = field(default_factory=dict)
    #: 门禁判定所用的"问题集合快照"指纹（workflow 决定 + legacy 忽略清单）。
    #: 落编辑锁时用它做 CAS：只有集合没变才允许宣告复核完成。
    problem_snapshot_token: str = ""
    coverage_available: bool = False
    coverage_blocking_total: Optional[int] = None
    #: 当前分析代际上的覆盖台账实例（obligation ledger 的逐项）。WP3-B 起，
    #: 完成门禁与人工补核都从这里取"哪些义务阻塞"——只允许这一处解析，
    #: 各处自行重算必然漂移（任务书 §十六）。
    coverage_items: List[CoverageObligationItem] = field(default_factory=list)
    #: 当前**有效会话**（进行中，否则当前代际上已完成）的人工补核决定，
    #: ``{obligation_id: 决定行}``。会话失效后旧决定留在库里供审计，
    #: 但不再出现在这里——门禁只读"这一次复核"的结论。
    obligation_decisions: Dict[str, Dict[str, Any]] = field(default_factory=dict)


# ---- 事实装载 ---------------------------------------------------------------


async def _load_state(
    conn: Any,
    slot_id: str,
    *,
    lock_slot: bool,
    lock_session: bool = False,
) -> Optional[ReviewState]:
    """装载门禁所需的全部事实。

    ``lock_slot=True`` 时先取槽位行锁（所有写路径必须为真）。锁顺序见模块顶部：
    槽位行锁是这里的第一把锁，之后才读分析代际——顺序反了就会出现
    "读完代际、再加会话锁"的窗口（重新分析在窗口里提交，本次复核把旧代际记成完成）。
    """
    if lock_slot:
        locked = await review_session_store.lock_slot_row(conn, slot_id)
        if locked is None:
            return None
    slot_row = await review_context_query.load_slot_row_for_review(conn, slot_id)
    if slot_row is None:
        return None

    analysis = await review_context_query.resolve_current_analysis(conn, slot_row)
    basis_token = (
        review_session_store.build_analysis_basis_token(
            analysis.job_uuid, analysis.analysis_revision
        )
        if analysis.job_uuid
        else None
    )

    active = await review_session_store.get_active_session(
        conn, slot_id, for_update=lock_session
    )
    completed = (
        await review_session_store.get_latest_completed_session(
            conn, slot_id, basis_token=basis_token
        )
        if basis_token
        else None
    )
    history = await review_session_store.list_history(conn, slot_id)

    state = ReviewState(
        slot_row=slot_row,
        slot_id=str(slot_row.get("slot_id") or slot_id),
        analysis=analysis,
        basis_token=basis_token,
        active_session=active,
        completed_session=completed,
        history=history,
    )
    _load_problem_set(state)
    _load_coverage(state)
    await _load_obligation_decisions(conn, state)
    return state


@dataclass
class _CommitEffects:
    """**PostgreSQL 提交成功之后**才允许执行的外部副作用。

    为什么必须把它们与数据库写入分开
    --------------------------------
    复核涉及三种存储，它们之间**没有**也**无法有**共同的原子事务：

    ==============================  ====================================
    存储                             内容
    ==============================  ====================================
    PostgreSQL                      ``review_sessions`` / ``material_slots``
                                    / ``analysis_jobs``
    文件 ``.issue_workflow.json``     ``review_locks``（复核完成后问题不可改）
    JSONL ``admin-actions.jsonl``     审计事件
    ==============================  ====================================

    ``conn.transaction()`` 只管第一行。把文件写入放进事务体内执行，会得到两种
    说谎的状态：DB 回滚了但文件已经改了（一个孤儿编辑锁把问题永久锁住），
    或者 DB 提交失败但审计里已经写了"复核完成"。

    因此本轮的规则是：

    **事务内只做数据库事实；文件副作用一律在提交成功之后执行。**

    唯一的例外是 ``lock_writes``：编辑锁必须在事务内写，因为它的失败必须
    让整个复核失败（fail-closed，见 ``_set_workflow_lock_or_raise``）；
    代价是"锁写了、提交失败"这个反向窗口，由提交失败时的
    ``clear_review_lock_if_matches`` 精确补偿。
    """

    #: 需要在事务内落下的编辑锁（失败即中止复核）。每项记录它的
    #: (job_id, review_session_id, analysis_basis_token)，供补偿时精确比较。
    lock_writes: List[Dict[str, Any]] = field(default_factory=list)
    #: 提交成功后才清除的编辑锁（每项含 job_uuid / review_session_id /
    #: analysis_basis_token，供精确比较；不带身份就清等于可能误删别人的锁）
    lock_clears: List[Dict[str, Any]] = field(default_factory=list)
    #: 提交成功后才写的业务成功审计
    audits: List[Dict[str, Any]] = field(default_factory=list)


@asynccontextmanager
async def _mutation_scope(conn: Any, effects: _CommitEffects):
    """复核写路径的事务范围：提交成功后执行文件副作用，提交失败则精确补偿。

    用法::

        effects = _CommitEffects()
        async with _mutation_scope(conn, effects):
            ... 只写数据库；需要锁时调 _set_workflow_lock_or_raise(effects, ...) ...
        # 到这里 PostgreSQL 已提交，文件副作用（清锁 / 审计）已执行

    提交失败（或事务体抛错）时：**不执行**任何 clears/audits，并把事务内已经
    写下的编辑锁按 session + basis 精确补偿掉——否则会留下"数据库说没复核、
    文件说已复核"的孤儿锁。

    已知边界：若调用方**自己**已经开了外层事务，本层不再开启新事务（`transaction_scope`
    直接复用），此时真正的提交时点由调用方掌握，文件副作用会提前到本层退出时执行。
    生产入口（路由）都在自动提交连接上调用，不存在这个情况；
    服务层自持事务边界也正是为了让"提交时点"这件事不必依赖调用方的记性。
    """
    try:
        async with review_session_store.review_transaction(conn):
            yield effects
    except BaseException:
        _compensate_lock_writes(effects)
        raise
    _run_post_commit_effects(effects)


def _run_post_commit_effects(effects: _CommitEffects) -> None:
    """执行提交后的文件副作用。两者都**不能**反向影响已经提交的数据库结论。"""
    for identity in effects.lock_clears:
        # 清锁失败是 fail-closed 方向：锁留着，用户暂时改不了问题（可重开复核），
        # 而不会出现"数据库说复核已失效、文件却把问题解锁了"。只记运维错误。
        #
        # 按 (job, session, basis) 精确比较：并发下文件里可能已经是**另一个会话**
        # 的锁，无条件按 job 清会把别人那条安全约束一起删掉。
        _clear_workflow_lock_if_matches(
            str(identity.get("job_uuid") or ""),
            review_session_id=str(identity.get("review_session_id") or ""),
            analysis_basis_token=str(identity.get("analysis_basis_token") or ""),
        )
    for event in effects.audits:
        _append_audit_best_effort(event)


def _compensate_lock_writes(effects: _CommitEffects) -> None:
    """DB 未提交 → 把本次写下的编辑锁按 session + basis 精确清掉。

    不能无条件按 job_id 清：并发下另一个会话可能已经写下它自己的锁，
    无条件清会把后来那次复核的安全约束一起删掉。
    """
    from src.services import issue_workflow_store

    for lock in effects.lock_writes:
        try:
            cleared = issue_workflow_store.clear_review_lock_if_matches(
                str(lock.get("job_uuid") or ""),
                review_session_id=lock.get("review_session_id"),
                analysis_basis_token=lock.get("analysis_basis_token"),
            )
        except Exception:  # noqa: BLE001 - 补偿是尽力而为，失败要让运维看见
            logger.exception(
                "Failed to compensate review lock after rollback for job %s",
                str(lock.get("job_uuid") or ""),
            )
            continue
        if cleared:
            logger.warning(
                "Compensated review lock after failed commit (job %s, session %s)",
                str(lock.get("job_uuid") or ""),
                str(lock.get("review_session_id") or ""),
            )


def _issue_decision_snapshot(job_uuid: str) -> Dict[str, Any]:
    """该任务当前的问题集合快照：``{decisions, ignored_issue_ids, token}``。

    调用方（``_load_problem_set``）只用它的 ``decisions`` 与 ``token``。
    读取发生在 ``issue_workflow_store`` 的文件锁之内，因此"决定"与"忽略清单"
    是同一时刻的一致视图。读不到时返回空快照 + 空 token：
    空 token 会让落锁时的 CAS 必然冲突（fail-closed），而不是把"读不到"当成"没问题"。
    """
    from src.services import issue_workflow_store

    try:
        return issue_workflow_store.get_job_problem_snapshot(job_uuid)
    except Exception:  # noqa: BLE001 - 读不到就按空快照 + 空 token 处理（fail-closed）
        logger.warning("Failed to read problem snapshot for job %s", job_uuid, exc_info=True)
        return {"decisions": {}, "ignored_issue_ids": [], "token": ""}


def _load_problem_set(state: ReviewState) -> None:
    """装载复核问题集合与工作流决定。

    问题集合的计算放在服务端**唯一**一处（``review_problem_set``），
    决定来自 ``issue_workflow_store``（问题人工处理的持久化来源）。
    两者合起来就是"还有哪些问题没有人表过态"。
    """
    job_uuid = str(state.analysis.job_uuid or "")
    if not job_uuid:
        return
    result_row = state.analysis.result_row or {}
    snapshot = _issue_decision_snapshot(job_uuid)
    ignored = _ignored_issue_ids(job_uuid)
    problems = collect_review_problems(
        job_uuid=job_uuid,
        ai_findings=result_row.get("ai_findings"),
        rule_findings=result_row.get("rule_findings"),
        merged_result=result_row.get("merged_result"),
        structured_ingest=state.analysis.structured_ingest,
        ignored_issue_ids=ignored,
    )
    state.problems = problems
    state.decisions = dict(snapshot.get("decisions") or {})
    state.problem_snapshot_token = str(snapshot.get("token") or "")


def _ignored_issue_ids(job_uuid: str) -> List[str]:
    """任务目录里的 legacy 忽略清单（``ignored_issue_ids``）。

    必须读它，否则门禁与工作台的**可见集合**不一致：工作台会把这些条目
    过滤掉（页面看不到），门禁却仍要求有人处理，用户就会遇到
    "页面上根本没有这条问题，却永远完不成复核"的死局。

    取不到（目录不存在、格式损坏）时返回空列表：宁可多要一个人工表态，
    也不能因为读不到忽略清单就静默放行。
    """
    try:
        from api import runtime

        job_dir = runtime.UPLOAD_ROOT / str(job_uuid)
        return sorted(str(item) for item in runtime.read_ignored_issue_ids(job_dir))
    except Exception:  # noqa: BLE001 - 忽略清单不是关键路径，读不到按"没有"处理
        logger.warning("Failed to read ignored issue ids for job %s", job_uuid, exc_info=True)
        return []


def _issue_decisions(job_uuid: str) -> Dict[str, str]:
    """该任务上所有已表态的问题状态（``{issue_id: status}``）。

    只读，不做权限过滤——调用方已经完成授权（路由层先判槽位可访问）。
    """
    from src.services import issue_workflow_store

    try:
        return issue_workflow_store.get_job_issue_decisions(job_uuid)
    except Exception:  # noqa: BLE001 - 读不到决定时按"都没表态"处理（fail-closed）
        logger.warning("Failed to read issue decisions for job %s", job_uuid, exc_info=True)
        return {}


def _load_coverage(state: ReviewState) -> None:
    """装载检查覆盖（obligation ledger）。

    取不到就是"不可用"，不是"0 条阻塞"。这两者在数字上不能长得一样：
    把未知读成零正是本项目反复出现的假完成来源（§四十六）。

    ``coverage_items`` 与计数在同一次解析里落定：门禁的"哪些义务待补核"
    与补核接口的"这条义务能不能表态"必须看到同一份清单。
    """
    coverage = build_coverage(state.analysis.result_meta.get("obligation_coverage"))
    state.coverage_available = bool(coverage.available)
    state.coverage_items = list(coverage.items) if coverage.available else []
    if coverage.available and coverage.summary is not None:
        state.coverage_blocking_total = coverage.summary.blocking_total


async def _load_obligation_decisions(conn: Any, state: ReviewState) -> None:
    """装载**当前有效会话**的人工补核决定（WP3-B）。

    有效会话 = 进行中的会话，否则当前分析代际上最近一条已完成会话。
    故意不按 (obligation_id, job_uuid) 聚合历史：上一次复核的补核结论
    不能漏进这一次——同一份材料可能有多个分析代际，决定属于"哪一次复核"
    必须毫不含糊（任务书 §十）。
    """
    effective = state.active_session or state.completed_session
    if effective is None:
        state.obligation_decisions = {}
        return
    rows = await review_obligation_store.list_session_decisions(
        conn, effective.get("review_session_id")
    )
    state.obligation_decisions = {
        str(row.get("obligation_id") or ""): row for row in rows if row.get("obligation_id")
    }


# ---- 门禁（纯函数，输入即事实） ---------------------------------------------


def _slot_identity_resolved(slot_row: Dict[str, Any]) -> bool:
    return bool(
        slot_identity_is_resolved(
            subject_org_id=slot_row.get("subject_org_id"),
            subject_kind=slot_row.get("subject_kind"),
            material_scope=slot_row.get("material_scope"),
            report_kind=slot_row.get("report_kind"),
            fiscal_year=slot_row.get("fiscal_year"),
            mapping_key=slot_row.get("mapping_key"),
        )
    )


def _problem_blockers(problems: List[ReviewProblem], decisions: Dict[str, str]) -> List[ReviewBlocker]:
    """按问题的人工处理状态统计阻塞项。

    三个终态（confirmed / no_issue / in_package）视为已解决；
    ``pending``（含**完全没有记录**）与 ``needs_review`` 全部阻塞。
    "数据库里没有这一行"绝不能被读成"已完成"——它只说明没有人表过态。
    """
    pending = 0
    needs_review = 0
    for problem in problems:
        if not problem.is_blocking_candidate:
            continue
        status = str(decisions.get(problem.issue_id) or "pending").strip() or "pending"
        if status in RESOLVED_ISSUE_STATUSES:
            continue
        if status == "needs_review":
            needs_review += 1
        else:
            # ``pending`` 以及**任何**未知/未表态的取值都计入待处理：
            # 数据库里没有这一行不等于已完成，把不认识的值当成"已解决"
            # 等于让一个拼错的状态码静默放行复核。
            pending += 1

    blockers: List[ReviewBlocker] = []
    if pending:
        blockers.append(ReviewBlocker(code=BLOCKER_PENDING_FINDINGS, count=pending))
    if needs_review:
        blockers.append(ReviewBlocker(code=BLOCKER_NEEDS_REVIEW_FINDINGS, count=needs_review))
    return blockers


def _blocking_obligations(state: ReviewState) -> List[CoverageObligationItem]:
    """引擎判定"阻塞门禁且未完成"的义务实例（WP3-B 起补核待办只含这些）。

    与 ``check_obligations._build_summary`` 的 ``blocking`` 口径完全一致：
    ``blocks_gate`` 且状态落在未完成集合里。门禁统计从这里出数，
    保证页面上"待补核清单"与 409 的 ``count`` 说的是同一批条目。
    """
    return [
        item
        for item in state.coverage_items
        if item.blocks_gate and item.status in UNRESOLVED_OBLIGATION_STATUSES
    ]


def _unhandled_obligations(state: ReviewState) -> List[CoverageObligationItem]:
    """尚未被人工处理的阻塞义务（完成门禁 WP3-B 口径的唯一判据）。

    ``verified_ok`` / ``verified_issue`` / ``not_applicable`` 算已处理；
    ``pending``、**没有决定记录**、以及任何未知决定值全部阻塞——
    与 ``_problem_blockers`` 同一条纪律：把不认识的值当成"已解决"，
    等于让一个拼错的状态码静默放行复核。
    """
    unhandled: List[CoverageObligationItem] = []
    for item in _blocking_obligations(state):
        row = state.obligation_decisions.get(item.obligation_id)
        current = str((row or {}).get("decision") or "").strip() or "pending"
        if current not in RESOLVED_OBLIGATION_DECISIONS:
            unhandled.append(item)
    return unhandled


def evaluate_start_gate(state: ReviewState) -> CompletionGate:
    """开复核门禁（§三十）。

    条件不满足时**不创建会话**，返回带 reason 的 409，而不是"先建一条再让
    完成门禁拦住"——那样会留下一条钉在不可复核事实上的活动会话。
    """
    blockers: List[ReviewBlocker] = []

    if (
        state.analysis.document_version_id is None
        and state.slot_row.get("current_document_version_id") is None
    ):
        blockers.append(ReviewBlocker(code=BLOCKER_NO_CURRENT_VERSION))
    if state.analysis.job_uuid is None:
        blockers.append(ReviewBlocker(code=BLOCKER_ANALYSIS_UNAVAILABLE))
    elif not state.analysis.completed:
        blockers.append(ReviewBlocker(code=BLOCKER_ANALYSIS_NOT_COMPLETED))
    if not _slot_identity_resolved(state.slot_row):
        blockers.append(ReviewBlocker(code=BLOCKER_IDENTITY_UNRESOLVED))
    if state.slot_row.get("caliber_conflict_candidate"):
        blockers.append(ReviewBlocker(code=BLOCKER_CALIBER_CONFLICT))

    return CompletionGate(can_complete=not blockers, blockers=blockers)


def evaluate_completion_gate(state: ReviewState) -> CompletionGate:
    """完成门禁（§三十四）：服务端重新计算全部条件，前端 disabled 不作数。

    顺序是固定的：先后回答"我看到的事实还是不是会话开始时的那些"，
    再回答"内容本身是否允许收口"。这样当两者同时不成立时，用户先看到
    "材料/分析已更新，请重新复核"——那才是他真正要做的动作。
    """
    blockers: List[ReviewBlocker] = []

    active = state.active_session
    current_version = state.slot_row.get("current_document_version_id")
    if active is not None:
        active_version = active.get("document_version_id")
        if current_version is None or int(active_version or 0) != int(current_version):
            blockers.append(ReviewBlocker(code=BLOCKER_DOCUMENT_VERSION_CHANGED))
        elif state.basis_token and active.get("analysis_basis_token") != state.basis_token:
            blockers.append(ReviewBlocker(code=BLOCKER_ANALYSIS_BASIS_CHANGED))
    elif state.completed_session is None:
        # 既没有活动会话，也没有"当前代际上已完成"的会话：还没开始复核。
        # 完成复核必须先有一个会话——"没有问题"不等于"人已经确认过"（§八十二）。
        blockers.append(ReviewBlocker(code=BLOCKER_REVIEW_NOT_STARTED))

    if state.analysis.job_uuid is None:
        blockers.append(ReviewBlocker(code=BLOCKER_ANALYSIS_UNAVAILABLE))
    elif not state.analysis.completed:
        blockers.append(ReviewBlocker(code=BLOCKER_ANALYSIS_NOT_COMPLETED))

    if not _slot_identity_resolved(state.slot_row):
        blockers.append(ReviewBlocker(code=BLOCKER_IDENTITY_UNRESOLVED))
    if state.slot_row.get("caliber_conflict_candidate"):
        blockers.append(ReviewBlocker(code=BLOCKER_CALIBER_CONFLICT))

    blockers.extend(_problem_blockers(state.problems, state.decisions))

    if not state.coverage_available:
        # "没有覆盖记录"与"覆盖完整"在数字上不能长得一样。
        # WP3-B 起这里**也不许**用人工补核绕过：覆盖不可用意味着"没有账本
        # 可表态"，只能回到分析侧修数据（任务书 §十二：禁止自动 not_applicable）。
        blockers.append(ReviewBlocker(code=BLOCKER_COVERAGE_UNAVAILABLE))
    else:
        # WP3-B 之后："阻塞义务存在"本身不再直接卡死完成门禁，卡的是
        # "存在**未处理**的阻塞义务"。人工在**当前复核会话**上逐条补核
        # （verified_ok / verified_issue / not_applicable）即可放行；
        # pending / 无记录 / 未知决定值继续阻塞。
        unhandled = _unhandled_obligations(state)
        if unhandled:
            blockers.append(
                ReviewBlocker(code=BLOCKER_BLOCKING_OBLIGATIONS, count=len(unhandled))
            )

    return CompletionGate(can_complete=not blockers, blockers=blockers)


# ---- 只读 -------------------------------------------------------------------


def _session_summary(row: Dict[str, Any]) -> ReviewSessionSummary:
    return ReviewSessionSummary(
        review_session_id=str(row.get("review_session_id") or ""),
        status=str(row.get("status") or ""),
        slot_id=str(row.get("slot_id") or ""),
        document_version_id=int(row.get("document_version_id") or 0),
        analysis_job_uuid=str(row.get("analysis_job_uuid") or ""),
        analysis_basis_token=str(row.get("analysis_basis_token") or ""),
        started_by=str(row.get("started_by") or ""),
        started_at=_iso(row.get("started_at")),
        completed_by=_opt_text(row.get("completed_by")),
        completed_at=_opt_iso(row.get("completed_at")),
        invalidated_at=_opt_iso(row.get("invalidated_at")),
        invalidated_reason=_opt_text(row.get("invalidated_reason")),
        review_result=dict(row.get("review_result") or {}),
    )


def _history_item(row: Dict[str, Any]) -> ReviewHistoryItem:
    return ReviewHistoryItem(
        review_session_id=str(row.get("review_session_id") or ""),
        status=str(row.get("status") or ""),
        document_version_id=int(row.get("document_version_id") or 0),
        analysis_job_uuid=str(row.get("analysis_job_uuid") or ""),
        analysis_basis_token=str(row.get("analysis_basis_token") or ""),
        started_by=str(row.get("started_by") or ""),
        started_at=_iso(row.get("started_at")),
        completed_by=_opt_text(row.get("completed_by")),
        completed_at=_opt_iso(row.get("completed_at")),
        invalidated_at=_opt_iso(row.get("invalidated_at")),
        invalidated_reason=_opt_text(row.get("invalidated_reason")),
    )


def _current_analysis_ref(state: ReviewState) -> CurrentAnalysisRef:
    return CurrentAnalysisRef(
        job_uuid=state.analysis.job_uuid,
        analysis_revision=state.analysis.analysis_revision,
        analysis_basis_token=state.basis_token,
        document_version_id=state.analysis.document_version_id,
        status=state.analysis.status,
        completed=state.analysis.completed,
        formal_issue_count=state.analysis.formal_issue_count,
    )


def _lifecycle_data(state: ReviewState) -> ReviewLifecycleData:
    return ReviewLifecycleData(
        slot_id=state.slot_id,
        current_document_version_id=(
            int(state.slot_row["current_document_version_id"])
            if state.slot_row.get("current_document_version_id") is not None
            else None
        ),
        current_analysis=_current_analysis_ref(state),
        current_session=(
            _session_summary(state.active_session) if state.active_session else None
        ),
        history=[_history_item(row) for row in state.history],
        completion_gate=evaluate_completion_gate(state),
        obligation_review=_obligation_review_block(state),
    )


def _obligation_review_block(state: ReviewState) -> "ObligationReviewBlock":
    """复核视角的补核待办清单：引擎判阻塞的义务 + 当前会话的人工决定。

    覆盖不可用时整体 ``available=False``（与 CoverageBlock 同一纪律：
    "没有覆盖记录"与"0 项待补核"在数字上不能长得一样）。
    """
    if not state.coverage_available:
        return ObligationReviewBlock(available=False, reason=BLOCKER_COVERAGE_UNAVAILABLE)
    items: List[ObligationReviewItem] = []
    for item in _blocking_obligations(state):
        row = state.obligation_decisions.get(item.obligation_id)
        items.append(
            ObligationReviewItem(
                obligation_id=item.obligation_id,
                group_id=item.group_id,
                group_title=item.group_title,
                title=item.title,
                status=item.status,
                reason=item.reason,
                reason_label=item.reason_label,
                detail=item.detail,
                decision=(str(row.get("decision") or "") or None) if row else None,
                decision_revision=(
                    int(row.get("revision") or 0) if row else None
                ),
                decided_by=(_opt_text(row.get("reviewer")) if row else None),
                decided_at=(_opt_iso(row.get("reviewed_at")) if row else None),
                note=(_opt_text(row.get("note")) if row else None),
                evidence_reference=(
                    _opt_text(row.get("evidence_reference")) if row else None
                ),
            )
        )
    return ObligationReviewBlock(
        available=True,
        reason=None,
        pending_total=len(_unhandled_obligations(state)),
        items=items,
    )


def _decision_record(row: Dict[str, Any]) -> ObligationDecisionRecord:
    return ObligationDecisionRecord(
        review_session_id=str(row.get("review_session_id") or ""),
        slot_id=str(row.get("slot_id") or ""),
        obligation_id=str(row.get("obligation_id") or ""),
        decision=str(row.get("decision") or ""),
        note=_opt_text(row.get("note")),
        evidence_reference=_opt_text(row.get("evidence_reference")),
        reviewer=str(row.get("reviewer") or ""),
        reviewed_at=_iso(row.get("reviewed_at")),
        revision=int(row.get("revision") or 1),
    )


def _manual_decision_summary(state: ReviewState) -> Dict[str, int]:
    """当前有效会话上各人工作出结论的计数（完成复核快照用）。"""
    summary = {
        "verified_ok": 0,
        "verified_issue": 0,
        "not_applicable": 0,
        "pending": 0,
    }
    for row in state.obligation_decisions.values():
        key = str(row.get("decision") or "").strip()
        summary[key if key in summary else "pending"] += 1
    return summary


async def load_review_by_slot(
    conn: Any, slot_id: str, *, expected_job_uuid: Optional[str] = None
) -> ReviewLifecycleData:
    """``GET /api/reviews/{slot_id}`` 的数据体。

    传 ``expected_job_uuid`` 时先校验它在**精确链路**上确实属于这个槽位，
    且正是当前分析：不匹配一律 409，绝不相信前端传来的配对关系（§二十八）。
    """
    state = await _load_state(conn, slot_id, lock_slot=False)
    if state is None:
        raise HTTPException(status_code=404, detail="material slot not found")
    if expected_job_uuid:
        _require_job_belongs_to_state(state, expected_job_uuid)
    return _lifecycle_data(state)


async def load_review_by_job(conn: Any, job_uuid: str) -> ReviewByJobData:
    """``GET /api/reviews?job_uuid=...``：按任务解析复核上下文。

    没有槽位链路（legacy 未关联任务）时返回 200 + ``available=false``，
    而不是 409：那不是错误状态，而是**正常但不可复核**的历史任务——
    页面照样能看旧审核内容，只是不能点「完成复核」（§七十三）。
    开/完成接口遇到同样情形会返回 409（那才是"请求了一个做不到的动作"）。
    """
    link = await review_context_query.resolve_slot_link_for_job(conn, job_uuid)
    if link is None:
        return ReviewByJobData(
            review_context=ReviewContextData(available=False, reason=ERROR_REVIEW_CONTEXT_UNAVAILABLE),
            review=None,
        )
    state = await _load_state(conn, link.slot_id, lock_slot=False)
    if state is None:
        return ReviewByJobData(
            review_context=ReviewContextData(available=False, reason=ERROR_REVIEW_CONTEXT_UNAVAILABLE),
            review=None,
        )
    context = ReviewContextData(available=True, slot_id=state.slot_id, job_uuid=link.job_uuid)
    return ReviewByJobData(review_context=context, review=_lifecycle_data(state))


def _require_job_belongs_to_state(state: ReviewState, job_uuid: str) -> None:
    """校验 ``job_uuid`` 属于该槽位的**当前分析**。

    两种不匹配都在这里拦下：
    - 该 job 根本不在这个槽位的链路上（攻击者同时有权看 slot-A 与 job-B，
      但 job-B 不属于 slot-A）；
    - 该 job 属于这个槽位，但不是当前分析（历史版本、或被更新的运行取代）。

    两者都必须 409，而不是"两个资源分别有权就组合成功"（§七十一）。
    """
    normalized = str(job_uuid or "").strip()
    current = str(state.analysis.job_uuid or "")
    if not normalized or normalized != current:
        raise ReviewLifecycleError(
            409,
            ERROR_REVIEW_CONTEXT_MISMATCH,
            "该任务不是这条材料当前的复核对象",
            job_uuid=normalized or None,
            current_job_uuid=current or None,
        )


# ---- 写路径 -----------------------------------------------------------------
#
# 四条写路径形状统一：
#
#     effects = _CommitEffects()
#     async with _mutation_scope(conn, effects):
#         只写数据库（槽位锁 / 会话 / 槽位状态）
#         _set_workflow_lock_or_raise(effects, ...)   # 编辑锁：事务内写，失败即中止
#         effects.lock_clears / effects.audits.append(...)  # 只登记，不在事务内执行
#     # PostgreSQL 提交成功 → 文件副作用才执行
#
# 理由见 ``_CommitEffects``：三种存储之间没有共同事务，唯一不说谎的做法是
# "文件副作用只在提交成功之后做"。


def _require_autocommit(conn: Any, *, operation: str) -> None:
    """复核 mutation 只能跑在**自动提交连接**上（自己拥有事务与提交时点）。

    为什么在代码里拒绝而不是写在文档里：``transaction_scope`` 在已有外层
    事务时会直接复用——此时复核函数返回时 PostgreSQL **还没有提交**，但
    ``_mutation_scope`` 已经把文件副作用执行了；调用方随后回滚，就重新造出
    上一轮刚修好的分裂：

        数据库：复核没完成
        文件：编辑锁已写/已清，audit 已记 success

    SAVEPOINT 不能用：RELEASE SAVEPOINT **不是** commit，复核的成败仍由
    外层事务决定。因此 public mutation 在最开始（**任何 DB 写入与文件副作用
    之前**）就 fail-fast。
    """
    if in_transaction(conn):
        raise RuntimeError(
            f"{operation} requires an autocommit connection; it owns its "
            "PostgreSQL transaction boundary (don't nest)"
        )


async def start_review(
    conn: Any,
    slot_id: str,
    *,
    actor: str,
    job_uuid: Optional[str] = None,
) -> ReviewMutationData:
    _require_autocommit(conn, operation="start_review")
    """开（或复用）复核会话。

    三个必须同时成立的性质：

    1. **先判门禁再建会话**：条件不满足时返回 409 + reason，不留下一条
       钉在不可复核事实上的活动会话；
    2. **幂等**：同一 (槽位, 版本, 代际) 已有活动会话时直接返回它。
       数据库的 partial unique index 是第二道门——两个浏览器同时点进来时，
       应用层的"先查再写"一定漏，唯一约束不会；
    3. **就地失效**：发现活动/已完成会话钉的版本或代际与当前不符时，
       先失效再建新会话。这样"重新分析之后又进来复核"不需要用户先理解失效规则。

    **整个函数体在一个事务里**（``_mutation_scope``）：槽位行锁与会话行锁
    必须活到"会话写入 + 槽位状态刷新"全部完成。裸放在 autocommit 连接上的
    ``FOR UPDATE`` 会在该条语句结束就释放，锁等于不存在——版本指针可以在
    "读到旧版本"与"写完成"之间被推进，产出"复核完成但复核的是旧版本"。

    门禁拒绝时**先提交事务再抛错**：过期事实的纠正（``_invalidate_stale_sessions``
    写下的失效）与"能不能开新复核"是两件事，前者不因后者失败而变得不成立。
    在事务内直接抛错会把纠正一起回滚掉，留下一条明明已经过期的"已完成复核"
    继续显示在界面上。
    """
    effects = _CommitEffects()
    outcome: Optional[ReviewMutationData] = None
    rejection: Optional[List[ReviewBlocker]] = None

    async with _mutation_scope(conn, effects):
        state = await _load_state(conn, slot_id, lock_slot=True, lock_session=True)
        if state is None:
            raise HTTPException(status_code=404, detail="material slot not found")

        if job_uuid:
            _require_job_belongs_to_state(state, job_uuid)

        await _invalidate_stale_sessions(conn, state, effects=effects, actor=actor)

        active = state.active_session
        if active is not None:
            # 复用前重新对照一次：``_invalidate_stale_sessions`` 只会清掉与当前
            # 不符的会话，能走到这里的活动会话一定与当前版本/代际一致。
            outcome = _mutation_data(state, active)
        else:
            gate = evaluate_start_gate(state)
            if not gate.can_complete:
                rejection = gate.blockers
            else:
                assert state.analysis.job_uuid and state.basis_token  # 门禁已保证
                created = await _insert_session_resilient(
                    conn, state, effects=effects, actor=actor
                )
                if created is not None:
                    await MaterialSlotService(conn).refresh_status(
                        state.slot_id,
                        analysis_state=ANALYSIS_DONE,
                        review_state=REVIEW_IN_PROGRESS,
                    )
                    effects.audits.append(
                        _audit_event(
                            actor,
                            "review.start",
                            "success",
                            state,
                            review_session_id=str(created.get("review_session_id") or ""),
                        )
                    )
                    # 新会话 ≠ 装载时那条有效会话：人工补核决定按会话重新绑定
                    # （新会话此刻一定没有任何决定）。
                    state.active_session = created
                    state.completed_session = None
                    await _load_obligation_decisions(conn, state)
                    outcome = _mutation_data(state, created)
                else:
                    # 并发下被别人先建好了：``_insert_session_resilient`` 已回读并登记审计。
                    existing = await review_session_store.get_active_session(
                        conn, state.slot_id
                    )
                    if existing is not None:
                        state.active_session = existing
                        state.completed_session = None
                        await _load_obligation_decisions(conn, state)
                        outcome = _mutation_data(state, existing)
                    else:
                        rejection = [ReviewBlocker(code=BLOCKER_REVIEW_NOT_STARTED)]

    if outcome is not None:
        return outcome
    raise ReviewLifecycleError(
        409, ERROR_START_BLOCKED, "当前无法开始复核", blockers=rejection or []
    )


async def _insert_session_resilient(
    conn: Any, state: ReviewState, *, effects: _CommitEffects, actor: str
) -> Optional[Dict[str, Any]]:
    """插入会话；并发下被别人抢先时返回 ``None``（并把复用登记进审计）。

    内层 ``conn.transaction()`` 在已有外层事务时会被 asyncpg 变成 **SAVEPOINT**：
    唯一约束冲突只回滚到该保存点，外层事务仍然可用，可以继续回读对方刚提交的
    那条会话。没有这一层的话，一次冲突就会把整个外层事务标记为失败，
    后续任何查询都报 ``current transaction is aborted``。

    注意这里是**显式** ``conn.transaction()`` 才会产生 SAVEPOINT；
    ``transaction_scope`` 自己不会创建保存点（它在已有事务时直接复用）。

    这里之所以还留着冲突分支：槽位行锁已经把并发 start 串行化了，
    理论上冲突不会发生；但"理论上不会"不能作为删掉兜底的理由——
    少一条兜底，代价是一条无人能解释的 500。
    """
    try:
        async with conn.transaction():
            return await review_session_store.insert_session(
                conn,
                slot_id=state.slot_id,
                document_version_id=state.analysis.document_version_id,
                analysis_job_uuid=state.analysis.job_uuid,
                analysis_basis_token=state.basis_token,
                started_by=actor,
            )
    except Exception as exc:  # noqa: BLE001 - 唯一约束冲突的形态由驱动决定
        existing = await review_session_store.get_active_session(conn, state.slot_id)
        if existing is None:
            raise
        logger.info("Concurrent review start detected for slot %s: %s", state.slot_id, exc)
        effects.audits.append(
            _audit_event(
                actor,
                "review.start",
                "reused",
                state,
                review_session_id=str(existing.get("review_session_id") or ""),
                concurrency="unique_violation",
            )
        )
        return None


async def complete_review(
    conn: Any, slot_id: str, *, actor: str, job_uuid: Optional[str] = None
) -> ReviewMutationData:
    _require_autocommit(conn, operation="complete_review")
    """完成复核：只有门禁全通过才写完成记录。

    **幂等**：同一个会话重复 complete 返回已经完成的同一结果，不新建第二条
    completed 会话，也不重复写审计与槽位状态（那些是业务副作用）。

    并发：两个 complete 同时到达时，两者都在槽位行锁上串行；后到者看到的
    ``status`` 已经是 ``completed``，因此走幂等分支返回同一结果——
    不会出现两条完成记录，也不会出现两次审计。

    **整个函数体在一个事务里**：槽位行锁必须从"读当前版本/代际"一直持到
    "会话写成 completed + 槽位状态刷成 completed"。这正是本函数最关键的时刻——
    若锁只活一条语句，版本指针可以在读完 V1 之后、写成 completed 之前被推进到
    V2，于是产出一条"已完成，但复核的是已经被替换掉的那一版文件"的记录。

    与数据库**不在同一事务**的两件事各有一个明确的时点：

    - **编辑锁**在事务内写，写不进去就中止（fail-closed，见
      ``_set_workflow_lock_or_raise``）；万一随后提交失败，提交失败路径会按
      (session, basis) 把它精确补偿掉；
    - **success 审计**在提交成功之后才写——写在事务体内会出现
      "审计说复核完成、数据库实际回滚了"。
    """
    effects = _CommitEffects()
    outcome: Optional[ReviewMutationData] = None

    async with _mutation_scope(conn, effects):
        state = await _load_state(conn, slot_id, lock_slot=True, lock_session=True)
        if state is None:
            raise HTTPException(status_code=404, detail="material slot not found")

        if job_uuid:
            _require_job_belongs_to_state(state, job_uuid)

        active = state.active_session
        if active is None and state.completed_session is not None:
            # 当前代际上已完成：幂等返回。**不**新建第二条完成记录、不新增审计。
            outcome = _mutation_data(state, state.completed_session, gate_override=True)
        else:
            gate = evaluate_completion_gate(state)
            if not gate.can_complete:
                # 把"为什么不能完成"原样回给前端：409 本身不是业务原因。
                raise ReviewLifecycleError(
                    409,
                    ERROR_COMPLETION_BLOCKED,
                    "当前无法完成复核",
                    blockers=gate.blockers,
                )

            assert active is not None and state.basis_token  # 门禁已保证有活动会话

            issue_counts = build_issue_counts(state.problems, state.decisions)
            manual_decisions = _manual_decision_summary(state)
            review_result = {
                "document_version_id": active.get("document_version_id"),
                "analysis_job_uuid": active.get("analysis_job_uuid"),
                "analysis_basis_token": active.get("analysis_basis_token"),
                "issue_counts": issue_counts,
                "coverage": {
                    "applicable_total": _coverage_summary_value(state, "applicable_total"),
                    "completed_total": _coverage_summary_value(state, "completed_total"),
                    # WP3-B 起这个字段的语义是"完成时仍未人工处理的阻塞义务数"
                    # （被门禁恒压成 0）；引擎判出的阻塞总数另记在
                    # ``blocking_engine_total``，两者分开才能回答"这次是全自动
                    # 通过，还是人工补核后通过"。
                    "blocking_count": len(_unhandled_obligations(state)),
                    "blocking_engine_total": state.coverage_blocking_total,
                    "blocking_obligations_closed_by": (
                        "automatic_and_manual"
                        if any(manual_decisions.values())
                        else "automatic_only"
                    ),
                    "manual_decisions": manual_decisions,
                },
            }

            completed = await review_session_store.complete_session(
                conn,
                review_session_id=active.get("review_session_id"),
                completed_by=actor,
                review_result=review_result,
            )
            if completed is None:
                # 行锁之内的状态已经不满足 ``status = 'in_progress'``：
                # 说明有人先一步完成或失效了这条会话。回读真实状态，不伪造成功。
                latest = await review_session_store.get_session_by_id(
                    conn, active.get("review_session_id")
                )
                if latest is None:
                    raise HTTPException(status_code=404, detail="review session not found")
                if latest.get("status") == REVIEW_STATUS_COMPLETED:
                    outcome = _mutation_data(state, latest, gate_override=True)
                else:
                    raise ReviewLifecycleError(
                        409,
                        ERROR_COMPLETION_BLOCKED,
                        "复核会话状态已变化，请刷新后重试",
                        blockers=[ReviewBlocker(code=BLOCKER_REVIEW_NOT_STARTED)],
                    )
            else:
                await MaterialSlotService(conn).refresh_status(
                    state.slot_id,
                    analysis_state=ANALYSIS_DONE,
                    review_state=REVIEW_COMPLETED,
                )
                # 编辑锁（"复核完成后问题不可再改"）是安全约束：它没落下就不允许
                # 宣称复核完成。写失败 → 抛错 → 整个事务回滚。
                _set_workflow_lock_or_raise(effects, state, actor=actor, session=completed)
                effects.audits.append(
                    _audit_event(
                        actor,
                        "review.complete",
                        "success",
                        state,
                        review_session_id=str(completed.get("review_session_id") or ""),
                        issue_total=issue_counts.get("total"),
                    )
                )
                outcome = _mutation_data(state, completed, gate_override=True)

    assert outcome is not None  # 上面的每条分支要么赋值要么抛错
    return outcome


async def reopen_review(
    conn: Any, slot_id: str, *, actor: str, job_uuid: Optional[str] = None
) -> ReviewMutationData:
    _require_autocommit(conn, operation="reopen_review")
    """显式重开复核：旧 completed 会话转为 ``invalidated``，另起一条新会话。

    "重新复核"必须是用户的**显式动作**（§七十六）：打开页面就把 completed
    改回 reviewing 会让"这份材料已经复核过"这一事实凭空消失。

    与 ``start_review`` 一样：整个函数体在一个事务里（锁必须活到写完新会话），
    且门禁拒绝时**先提交再抛错**——"把旧复核作废"是用户显式要求的动作，
    它不因为"新的复核开不起来"而变得不成立。

    旧编辑锁的清除登记在 ``effects.lock_clears`` 里、**提交之后**才执行：
    若事务最终回滚（旧 completed 仍然有效），提前清锁会让那份"已完成"在文件层面
    失去"问题不可改"的约束，出现"数据库说已完成、文件说可以改"的分裂。
    """
    effects = _CommitEffects()
    outcome: Optional[ReviewMutationData] = None
    rejection: Optional[List[ReviewBlocker]] = None

    async with _mutation_scope(conn, effects):
        state = await _load_state(conn, slot_id, lock_slot=True, lock_session=True)
        if state is None:
            raise HTTPException(status_code=404, detail="material slot not found")

        if job_uuid:
            _require_job_belongs_to_state(state, job_uuid)

        previous = state.active_session or state.completed_session
        if previous is not None:
            await review_session_store.invalidate_session(
                conn,
                review_session_id=previous.get("review_session_id"),
                reason=INVALIDATION_REASON_REVIEW_REOPENED,
            )
            effects.lock_clears.append(_lock_identity(previous))
            effects.audits.append(
                _audit_event(
                    actor,
                    "review.reopen",
                    "invalidated_previous",
                    state,
                    review_session_id=str(previous.get("review_session_id") or ""),
                    reason=INVALIDATION_REASON_REVIEW_REOPENED,
                )
            )

        gate = evaluate_start_gate(state)
        if not gate.can_complete:
            rejection = gate.blockers
        else:
            assert state.analysis.job_uuid and state.basis_token
            created = await _insert_session_resilient(
                conn, state, effects=effects, actor=actor
            )
            if created is not None:
                await MaterialSlotService(conn).refresh_status(
                    state.slot_id,
                    analysis_state=ANALYSIS_DONE,
                    review_state=REVIEW_IN_PROGRESS,
                )
                effects.audits.append(
                    _audit_event(
                        actor,
                        "review.reopen",
                        "success",
                        state,
                        review_session_id=str(created.get("review_session_id") or ""),
                    )
                )
                # 旧会话刚被显式失效：有效会话换成新会话，补核决定同步重绑
                # （旧会话的决定留在库里供审计，但不再参与本次门禁）。
                state.active_session = created
                state.completed_session = None
                await _load_obligation_decisions(conn, state)
                outcome = _mutation_data(state, created)
            else:
                existing = await review_session_store.get_active_session(conn, state.slot_id)
                if existing is not None:
                    state.active_session = existing
                    state.completed_session = None
                    await _load_obligation_decisions(conn, state)
                    outcome = _mutation_data(state, existing)
                else:
                    rejection = [ReviewBlocker(code=BLOCKER_REVIEW_NOT_STARTED)]

    if outcome is not None:
        return outcome
    raise ReviewLifecycleError(
        409, ERROR_START_BLOCKED, "当前无法重新开始复核", blockers=rejection or []
    )


def _obligation_evidence_error(
    decision: str, note: Optional[str], evidence_reference: Optional[str]
) -> Optional[Dict[str, Any]]:
    """依据纪律（任务书 §十）：resolved 决定不允许"零依据"关闭门禁。

    规则（独立评审裁决版）：

    - ``pending``：允许空依据（它只是"置回待处理"，不产生放行效果）；
    - ``verified_ok``：``note`` 或 ``evidence_reference`` 至少填一项——
      "我核对过"必须能回答"核对了什么/在哪里"；
    - ``verified_issue``：``note`` 必填——"确认存在问题"却不写发现了什么问题，
      审计无法回答"这项问题到底是什么"（任务书允许 note-OR-evidence 的放宽，
      但"问题描述"属于补核结论本体，不是可选附件）；
    - ``not_applicable``：``note`` 必填——人工改变适用性必须解释
      "为什么这条义务对本材料不适用"。

    一律 422：请求结构合法、缺的是业务必填字段，属输入校验，不是状态冲突
    （任务书 §十二 的 422/409 二选一，本批全接口统一 422）。
    """
    if decision == OBLIGATION_DECISION_PENDING:
        return None
    if decision == OBLIGATION_DECISION_VERIFIED_OK:
        if note or evidence_reference:
            return None
        message = "补核通过必须填写补核说明或证据位置（页码/表名/章节）"
    elif decision == OBLIGATION_DECISION_VERIFIED_ISSUE:
        if note:
            return None
        message = "确认存在问题必须写明发现了什么问题（补核说明）"
    elif decision == OBLIGATION_DECISION_NOT_APPLICABLE:
        if note:
            return None
        message = "判定不适用必须写明原因（为什么本材料不涉及该项检查）"
    else:
        # 未知决定值在参数校验处已被拒绝；这里是防御性兜底，永远不会到达。
        return None
    return {
        "error": ERROR_OBLIGATION_DECISION_EVIDENCE_REQUIRED,
        "message": message,
        "decision": decision,
    }


def _prepare_obligation_decision(
    state: ReviewState, obligation_id: str
) -> Optional[HTTPException]:
    """判定这条义务当前**能不能**被人工表态；可以则返回 ``None``。

    两层判据（都不通过即拒绝，而不是"先记下来再说"）：

    - 义务必须出现在**当前分析代际的覆盖台账**里：账本里没有它，
      人工结论就没有可依附的事实（覆盖不可用时 items 为空，天然全部 404）；
    - 义务必须仍被引擎判为"阻塞且未完成"：已自动完成的义务没有东西
      需要人工表态——"给已完成义务补个通过"会让引擎台账与人工台账
      各说各话。
    """
    if obligation_id not in {item.obligation_id for item in state.coverage_items}:
        return HTTPException(status_code=404, detail="obligation not found in current review")
    if obligation_id not in {item.obligation_id for item in _blocking_obligations(state)}:
        return ReviewLifecycleError(
            409,
            ERROR_OBLIGATION_NOT_BLOCKING,
            "该检查义务当前不由引擎判定为待补核（已自动完成或不适用）",
        )
    return None


async def _write_obligation_decision(
    conn: Any,
    state: ReviewState,
    active_session: Dict[str, Any],
    *,
    obligation_id: str,
    decision: str,
    note: Optional[str],
    evidence_reference: Optional[str],
    expected_revision: Optional[int],
    actor: str,
) -> tuple:
    """执行决定写入，返回 ``(行, 审计动作)``；并发/过期一律 409，不静默覆盖。

    两条并发防线各自的职责（任务书 §十五）：

    - **唯一约束** ``uq_review_obligation_decisions_scope``：两个复核人同时
      首次表态时，后到者的 INSERT 命中冲突返回 None → 409；
    - **乐观锁** ``revision``：改写已有决定必须携带读到的 revision，
      带错/不带 → 409（响应里附 ``current_revision`` 供展示层提示刷新）。
    """
    existing = state.obligation_decisions.get(obligation_id)
    if existing is None:
        if expected_revision is not None:
            # 带着 revision 来却查不到行：客户端拿到的是过期视图。
            raise ReviewLifecycleError(
                409,
                ERROR_OBLIGATION_DECISION_CONFLICT,
                "该义务的补核状态已变化，请刷新后重试",
            )
        row = await review_obligation_store.insert_decision(
            conn,
            review_session_id=active_session.get("review_session_id"),
            slot_id=state.slot_id,
            obligation_id=obligation_id,
            decision=decision,
            note=note,
            evidence_reference=evidence_reference,
            reviewer=actor,
        )
        if row is None:
            # 并发下别人已先写入（唯一约束兜底）。先到者的结论优先。
            raise ReviewLifecycleError(
                409,
                ERROR_OBLIGATION_DECISION_CONFLICT,
                "该义务已有其他复核人表态，请刷新后查看再决定",
            )
        return row, "obligation.reviewed"

    current_revision = int(existing.get("revision") or 0)
    if expected_revision is None or int(expected_revision) != current_revision:
        # 改写必须基于最新读数：拿旧 revision（或不带）来改，
        # 等于在不知情时覆盖别人的结论。
        raise ReviewLifecycleError(
            409,
            ERROR_OBLIGATION_DECISION_CONFLICT,
            "该义务的补核状态已变化，请刷新后重试",
            current_revision=current_revision,
        )
    row = await review_obligation_store.update_decision_if_revision_matches(
        conn,
        decision_record_id=existing.get("decision_record_id"),
        expected_revision=expected_revision,
        decision=decision,
        note=note,
        evidence_reference=evidence_reference,
        reviewer=actor,
    )
    if row is None:
        raise ReviewLifecycleError(
            409,
            ERROR_OBLIGATION_DECISION_CONFLICT,
            "该义务的补核状态已变化，请刷新后重试",
            current_revision=current_revision,
        )
    return row, "obligation.updated"


async def decide_obligation(
    conn: Any,
    slot_id: str,
    *,
    obligation_id: str,
    decision: str,
    actor: str,
    note: Optional[str] = None,
    evidence_reference: Optional[str] = None,
    expected_revision: Optional[int] = None,
    job_uuid: Optional[str] = None,
) -> ObligationDecisionData:
    _require_autocommit(conn, operation="decide_obligation")
    """人工补核一条检查义务（WP3-B），写入绑定**当前活动会话**的数据库决定。

    四条红线逐条落点：

    1. **持久化**：写入 ``review_obligation_decisions``（PostgreSQL），
       不落前端 state / localStorage / 临时 JSON 文件；
    2. **绑定会话**：决定挂到服务端解析出的**当前活动复核会话**上，
       前端不传、也不许传 review_session_id——"这是哪一次复核上的结论"
       只能由服务端认定（与 ``ReviewActionRequest`` 的 job_uuid 口径一致）；
    3. **人工显式**：只有本函数能写入决定。系统任何路径都不得把
       "覆盖不可用 / 取数不足 / AI 通过"自动折算成人工结论；
    4. **并发不静默覆盖**：首次表态撞唯一约束、改写未带当前 ``revision``，
       一律 409（``obligation_decision_conflict``）。宁可让人刷新后再确认
       一次，也不能让后到者的写入悄悄冲掉先到者的结论。

    **整个函数体在一个事务里**（``_mutation_scope``，槽位行锁贯穿始终）：
    补核与完成复核被同一把槽位锁串行化——不会出现"补核写进去了，
    但完成判定看的是写入前的旧决定"，也不会反过来。
    """
    normalized_obligation = str(obligation_id or "").strip()
    normalized_decision = str(decision or "").strip()
    if not normalized_obligation:
        raise HTTPException(status_code=422, detail="obligation_id is required")
    if normalized_decision not in OBLIGATION_DECISIONS:
        # 路由层的 pydantic 校验已经会用 422 拦住；服务层再校验一次
        # 是纵深防御（其他调用方不走路由）。沿用 issue 工作流的 400 口径。
        raise HTTPException(status_code=400, detail="invalid obligation decision")
    note_text = str(note or "").strip() or None
    evidence_text = str(evidence_reference or "").strip() or None

    # 依据纪律在任何数据库写入之前判定：读都没有必要做——缺依据的请求
    # 立刻 422，不给它接触并发窗口的机会。
    evidence_error = _obligation_evidence_error(normalized_decision, note_text, evidence_text)
    if evidence_error is not None:
        raise HTTPException(status_code=422, detail=evidence_error)

    effects = _CommitEffects()
    outcome: Optional[ObligationDecisionData] = None
    rejection: Optional[HTTPException] = None

    async with _mutation_scope(conn, effects):
        state = await _load_state(conn, slot_id, lock_slot=True, lock_session=True)
        if state is None:
            raise HTTPException(status_code=404, detail="material slot not found")

        if job_uuid:
            _require_job_belongs_to_state(state, job_uuid)

        # 与 start_review 同一条纪律：发现活动/已完成会话钉着过期事实，
        # 先就地失效、**提交**，再拒绝——补核写进一条已经过期的会话等于
        # 把结论挂在死记录上；而"纠正过期事实"不因为"这次补核被拒绝"
        # 就变得不成立（在事务内直接抛错会把纠正一起回滚掉）。
        await _invalidate_stale_sessions(conn, state, effects=effects, actor=actor)

        active = state.active_session
        if active is None:
            if state.completed_session is not None:
                # 复核完成后补核同样锁定（与"复核完成后问题不可再改"同源）。
                # 要先改结论，先显式重新复核。
                rejection = ReviewLifecycleError(
                    409,
                    ERROR_REVIEW_COMPLETED_LOCKED,
                    "复核已完成，如需调整补核结论请先重新复核",
                )
            else:
                rejection = ReviewLifecycleError(
                    409,
                    ERROR_REVIEW_NOT_ACTIVE,
                    "请先开始复核，再处理检查义务",
                )
        else:
            rejection = _prepare_obligation_decision(state, normalized_obligation)
            if rejection is None:
                row, action = await _write_obligation_decision(
                    conn,
                    state,
                    active,
                    obligation_id=normalized_obligation,
                    decision=normalized_decision,
                    note=note_text,
                    evidence_reference=evidence_text,
                    expected_revision=expected_revision,
                    actor=actor,
                )
                # 让本函数返回的门禁反映刚写入的决定（同事务内的一致视图）。
                state.obligation_decisions[normalized_obligation] = row
                effects.audits.append(
                    _obligation_audit_event(
                        actor,
                        action,
                        "success",
                        state,
                        obligation_id=normalized_obligation,
                        decision=normalized_decision,
                        revision=int(row.get("revision") or 1),
                        note=note_text,
                        evidence_reference=evidence_text,
                    )
                )
                gate = evaluate_completion_gate(state)
                outcome = ObligationDecisionData(
                    slot_id=state.slot_id,
                    current_document_version_id=(
                        int(state.slot_row["current_document_version_id"])
                        if state.slot_row.get("current_document_version_id") is not None
                        else None
                    ),
                    session=_session_summary(active),
                    decision=_decision_record(row),
                    completion_gate=gate,
                    blockers=gate.blockers,
                )

    if outcome is not None:
        return outcome
    assert rejection is not None  # 上面的每条分支要么给结果，要么给拒绝
    raise rejection

async def invalidate_review(
    conn: Any, slot_id: str, *, reason: Optional[str] = None, actor: str = "system"
) -> int:
    _require_autocommit(conn, operation="invalidate_review")
    """让该槽位上"已经对不上当前事实"的会话失效，并把槽位状态交回状态机重算。

    失效的判据是两个，任一成立即失效（原因码分别记录，因为用户要做的动作不同）：

    - 会话钉的 ``document_version_id`` 已不是槽位的当前版本 → ``document_version_changed``；
    - 会话钉的 ``analysis_basis_token`` 已不是当前分析代际 → ``analysis_basis_changed``。

    这是"防御性懒失效"的公开入口：版本替换与重新分析都在写入侧主动调用了失效钩子，
    但钩子可能因为进程重启、数据库短暂不可用而没有生效；每次读/写复核时再走一遍
    本方法，正确性就不依赖"钩子一定跑过"。

    槽位状态**不由本方法直接指定**：只把 ``review_state=REVIEW_REQUIRED`` 交给
    ``refresh_status``，身份未解决 / 口径冲突 / 没有当前文件的槽位，
    状态机会给出它们各自正确的状态。

    整个函数体在一个事务里：失效写入与它在同一把槽位锁之下读到的版本/代际
    必须是一个一致快照，否则"判它过期"这件事本身就可能依据了旧事实。
    清锁与审计在提交之后执行。
    """
    effects = _CommitEffects()
    invalidated_count = 0

    async with _mutation_scope(conn, effects):
        state = await _load_state(conn, slot_id, lock_slot=True, lock_session=True)
        if state is None:
            raise HTTPException(status_code=404, detail="material slot not found")

        targeted = state.active_session or state.completed_session
        if targeted is not None and reason:
            # 调用方指定了原因码时，作用对象收窄到"当前那一条会话"：
            # 这用于"已经知道该作废哪一条"的场景。
            invalidated = await review_session_store.invalidate_session(
                conn, review_session_id=targeted.get("review_session_id"), reason=reason
            )
            if invalidated is not None:
                effects.lock_clears.append(_lock_identity(targeted))
                effects.audits.append(
                    _audit_event(
                        actor,
                        "review.invalidate",
                        "success",
                        state,
                        review_session_id=str(targeted.get("review_session_id") or ""),
                        reason=reason,
                    )
                )
                await MaterialSlotService(conn).refresh_status(
                    slot_id, review_state=REVIEW_REQUIRED
                )
                invalidated_count = 1
        else:
            invalidated_count = await _invalidate_stale_sessions(
                conn, state, effects=effects, actor=actor
            )
            if invalidated_count:
                await MaterialSlotService(conn).refresh_status(
                    slot_id, review_state=REVIEW_REQUIRED
                )

    return invalidated_count


async def _invalidate_stale_sessions(
    conn: Any, state: ReviewState, *, effects: _CommitEffects, actor: str
) -> int:
    """把"钉着过期版本/代际"的会话就地失效（懒失效层），返回失效条数。

    两种情形分开记原因：版本被替换（``document_version_changed``）与
    分析重新跑过（``analysis_basis_changed``）是两件不同的事，
    用户要做的动作也不同（重新看文件 vs 重新看分析结论）。

    失效之后会把 ``state`` 里对应的字段清空：调用方（``start_review``）随后
    要据此创建新会话，留着一条"已经被作废的旧会话"会让它误以为已有活动会话。

    清锁与审计只登记进 ``effects``，由提交成功之后统一执行——
    事务回滚时旧会话仍然有效，提前清锁会让它在文件层面失去约束。
    """
    invalidated_count = 0
    for session in (state.active_session, state.completed_session):
        if session is None:
            continue
        version_changed = (
            state.slot_row.get("current_document_version_id") is None
            or int(session.get("document_version_id") or 0)
            != int(state.slot_row.get("current_document_version_id") or 0)
        )
        if version_changed:
            reason = INVALIDATION_REASON_DOCUMENT_VERSION_CHANGED
        elif state.basis_token and session.get("analysis_basis_token") != state.basis_token:
            reason = INVALIDATION_REASON_ANALYSIS_BASIS_CHANGED
        else:
            continue
        await review_session_store.invalidate_session(
            conn,
            review_session_id=session.get("review_session_id"),
            reason=reason,
        )
        invalidated_count += 1
        effects.lock_clears.append(_lock_identity(session))
        effects.audits.append(
            _audit_event(
                actor,
                "review.invalidate",
                "success",
                state,
                review_session_id=str(session.get("review_session_id") or ""),
                reason=reason,
            )
        )
        if session is state.active_session:
            state.active_session = None
        if session is state.completed_session:
            state.completed_session = None
    if invalidated_count:
        # 有效会话的身份可能已变化（或变空）——人工补核决定绑定的是**会话**，
        # 不随失效动作自动迁移（任务书 §十）。重新按当前有效会话装载一次，
        # 错误地沿用旧会话的决定等于让上一次复核的结论漏进这一次。
        await _load_obligation_decisions(conn, state)
    return invalidated_count


# ---- 主动失效钩子（写入侧） -------------------------------------------------


async def invalidate_reviews_for_analysis_restart(
    job_uuid: str, *, reason: str = INVALIDATION_REASON_ANALYSIS_RESTARTED
) -> Dict[str, Any]:
    """重新分析启动时：让该任务上的活动/已完成复核立刻失效。

    为什么必须在**写入侧**主动做这一件事：只靠"下次打开页面才发现"时，
    页面会理直气壮地显示「已完成复核」，而它复核的是上一代分析结果。
    主动失效让界面立刻显示"需要重新复核"。

    同时清掉"复核完成后禁止改问题"的编辑锁：锁的语义是"当前代际上的复核
    已经收口"，代际一变锁就该开——否则用户会遇到"复核已失效但问题还是改不了"。

    这里**不**递增 ``analysis_revision``：那是"结果落库"路径的职责
    （见 ``clear_analysis_result_fingerprint`` 的注释）。本钩子只做两件事：
    清指纹（让下一次落库必定换代）与失效旧复核。

    跨存储纪律与前四条写路径一致：清锁在事务**提交之后**做，
    且按 (session, basis) 精确比较——无条件按 job 清会误删并发下新会话的锁。
    """
    from src.db.connection import DatabaseConnection

    # 变量名带安全后缀是有意的：日志门禁要求"插进 message 的裸变量名必须是
    # 可判定安全的标量"（前缀/后缀白名单）。用 `job_uuid` / `invalidated_count`
    # 这类名字，既满足门禁，也如实表达它们只是标识符与计数，不含材料原文。
    job_uuid = str(job_uuid or "").strip()
    if not job_uuid:
        return {"job_uuid": "", "sessions_invalidated": 0, "slot_id": None}

    # 没有配库就没有复核存储，更不会有复核会话可失效。
    # 直接返回而不是去 acquire（那会在未配库时抛异常）：钩子挂在**每一次**分析
    # 启动上，而仓库的多数测试都不配库，用异常路径收场只会把日志刷满噪声，
    # 让真正的失败被淹没。
    if not (os.getenv("DATABASE_URL") or "").strip():
        return {
            "job_uuid": job_uuid,
            "slot_id": None,
            "sessions_invalidated": 0,
            "reason": reason,
            "skipped": "database_not_configured",
        }

    invalidated_sessions: List[Dict[str, Any]] = []
    slot_uuid: Optional[str] = None
    conn = await DatabaseConnection.acquire()
    try:
        async with conn.transaction():
            link = await review_context_query.resolve_slot_link_for_job(conn, job_uuid)
            slot_uuid = link.slot_id if link is not None else None
            if slot_uuid is not None:
                # 锁顺序：槽位行锁必须在会话行之前（模块顶部 LOCK ORDER）。
                await review_session_store.lock_slot_row(conn, slot_uuid)
            invalidated_sessions = await review_session_store.invalidate_sessions_for_job(
                conn, job_uuid, reason=reason
            )
            await review_session_store.clear_analysis_result_fingerprint(conn, job_uuid)
            if slot_uuid is not None and invalidated_sessions:
                await MaterialSlotService(conn).refresh_status(
                    slot_uuid, review_state=REVIEW_REQUIRED
                )
    finally:
        await DatabaseConnection.release(conn)

    # 提交之后：按会话身份精确清锁（fail-closed：失败保留锁，只记运维错误）。
    for item in invalidated_sessions:
        _clear_workflow_lock_if_matches(
            job_uuid,
            review_session_id=str(item.get("review_session_id") or ""),
            analysis_basis_token=str(item.get("analysis_basis_token") or ""),
        )
    invalidated_count = len(invalidated_sessions)
    if invalidated_count:
        logger.info(
            "Review sessions invalidated for reanalysis of job %s (invalidated_count=%s)",
            job_uuid,
            invalidated_count,
        )
    return {
        "job_uuid": job_uuid,
        "slot_id": slot_uuid,
        "sessions_invalidated": invalidated_count,
        "reason": reason,
    }


# ---- 工作流编辑锁（与 issue_workflow_store 同一份持久化） -------------------


def _lock_identity(session: Dict[str, Any]) -> Dict[str, Any]:
    """会话 → 清除编辑锁所需的身份三元组。

    必须带上 session 与 basis：清除只按 job_id 做时，并发下会误删**后来会话**
    留下的锁——那等于把"复核已完成、问题不可改"这条安全约束从别人身上摘掉。
    """
    return {
        "job_uuid": str(session.get("analysis_job_uuid") or ""),
        "review_session_id": str(session.get("review_session_id") or ""),
        "analysis_basis_token": str(session.get("analysis_basis_token") or ""),
    }


def _set_workflow_lock_or_raise(
    effects: _CommitEffects, state: ReviewState, *, actor: str, session: Dict[str, Any]
) -> None:
    """在**事务内**落下"问题不可再改"的编辑锁；失败即中止本次复核（fail-closed）。

    三个设计点：

    1. **锁与 issue 决定写在同一份文件、同一把文件锁下**。跨存储做不到原子事务，
       所以让锁跟着它保护的数据走：否则"复核刚完成、锁还没落下"的那一瞬间，
       一次 issue 修改会静默穿过去（§五十七）。
    2. **失败必须让复核失败**。此前这里吞掉异常，结果是
       "PostgreSQL 提交 review_session=completed，但编辑锁没落下"——
       用户仍然可以改一份"已完成复核"的问题。编辑锁是**安全约束**，
       它没落下就不允许宣称复核完成。
    3. **记下锁的身份**（job + session + basis），供提交失败时精确补偿。
       无条件按 job 清锁会在并发下误删后来会话留下的锁。
    4. **CAS**：写入之前先比对问题集合快照。若快照已变（别人把 issue 改回
       待处理，或把它加入忽略清单），**不写锁**，抛 409 review_workflow_changed。
       比较与写入在同一把 ``_state_lock`` 之下完成（``set_review_lock_if_problem_snapshot_matches``）。
    """
    from src.services import issue_workflow_store

    job_uuid = str(state.analysis.job_uuid or "")
    review_session_id = str(session.get("review_session_id") or "")
    basis_token = str(session.get("analysis_basis_token") or "")
    try:
        outcome = issue_workflow_store.set_review_lock_if_problem_snapshot_matches(
            job_uuid,
            expected_token=state.problem_snapshot_token,
            slot_id=state.slot_id,
            review_session_id=review_session_id,
            analysis_basis_token=basis_token,
            completed_by=actor,
        )
    except Exception as exc:  # noqa: BLE001 - 任何写入失败都必须阻止复核成功
        logger.error(
            "Failed to persist review edit lock for job %s; aborting completion",
            job_uuid,
            exc_info=True,
        )
        raise ReviewLifecycleError(
            503,
            ERROR_REVIEW_LOCK_UNAVAILABLE,
            "无法记录复核锁定状态，本次复核未完成，请稍后重试",
        ) from exc

    if str(outcome.get("status") or "") != "written":
        # 门禁判定所用的问题集合在落锁之前变了（有人改了 issue 状态，或把它加进
        # 忽略清单）。这时**不能**让本次复核成立：它锁住的将不再是被判定的那份问题集合。
        # 放回问题集合并让外层回滚。
        raise ReviewLifecycleError(
            409,
            ERROR_REVIEW_WORKFLOW_CHANGED,
            "问题处理状态已发生变化，请刷新后重新确认",
            expected_problem_token=str(outcome.get("expected_decision_token") or ""),
            current_problem_token=str(outcome.get("current_decision_token") or ""),
        )

    effects.lock_writes.append(
        {
            "job_uuid": job_uuid,
            "review_session_id": review_session_id,
            "analysis_basis_token": basis_token,
        }
    )


def _clear_workflow_lock_if_matches(
    job_uuid: str, *, review_session_id: str, analysis_basis_token: str
) -> bool:
    """按 (job, session, basis) 精确清除编辑锁。

    **fail-closed 方向**：清锁失败时保留锁（用户暂时改不了问题，可以重开复核），
    而不会出现"数据库说复核已失效、文件却把问题解锁了"。因此失败只记运维错误，
    不抛给调用方——它发生在提交之后，抛出去也改不了已提交的数据库结论。
    """
    from src.services import issue_workflow_store

    job_uuid = str(job_uuid or "").strip()
    if not job_uuid:
        return False
    try:
        return bool(
            issue_workflow_store.clear_review_lock_if_matches(
                job_uuid,
                review_session_id=review_session_id,
                analysis_basis_token=analysis_basis_token,
            )
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "Failed to clear review edit lock for job %s; the lock stays "
            "(issues remain non-editable until the review is reopened)",
            job_uuid,
        )
        return False


# ---- 小工具 -----------------------------------------------------------------


def _mutation_data(
    state: ReviewState, session: Dict[str, Any], *, gate_override: bool = False
) -> ReviewMutationData:
    gate = evaluate_completion_gate(state)
    if gate_override:
        gate = CompletionGate(can_complete=True, blockers=[])
    return ReviewMutationData(
        slot_id=state.slot_id,
        current_document_version_id=(
            int(state.slot_row["current_document_version_id"])
            if state.slot_row.get("current_document_version_id") is not None
            else None
        ),
        session=_session_summary(session),
        completion_gate=gate,
        blockers=gate.blockers,
    )


def _coverage_summary_value(state: ReviewState, key: str) -> Optional[int]:
    coverage = build_coverage(state.analysis.result_meta.get("obligation_coverage"))
    if not coverage.available or coverage.summary is None:
        return None
    return getattr(coverage.summary, key, None)


def _audit_event(
    actor: str,
    action: str,
    result: str,
    state: ReviewState,
    *,
    review_session_id: str,
    **extra: Any,
) -> Dict[str, Any]:
    """构造一条审计事件（纯函数，不写盘）。

    ``details`` 只放安全字段（槽位、版本、任务、原因）——**不放** finding 全文、
    PDF 原文、raw_response。审计日志是"谁在什么时候做了什么"，不是第二份分析结果。

    构造与写入分开，是为了让业务成功审计能在**PostgreSQL 提交之后**才落盘：
    写在事务体内会出现"审计说复核完成、数据库实际回滚了"。
    """
    details: Dict[str, Any] = {
        "slot_id": state.slot_id,
        "document_version_id": state.analysis.document_version_id,
        "analysis_job_uuid": state.analysis.job_uuid,
        "analysis_basis_token": state.basis_token,
        "review_session_id": review_session_id,
    }
    details.update({key: value for key, value in extra.items() if value is not None})
    return {
        "action": action,
        "actor": actor,
        "result": result,
        "resource_type": "review_session",
        "resource_id": review_session_id,
        "details": details,
    }


def _obligation_audit_event(
    actor: str,
    action: str,
    result: str,
    state: ReviewState,
    *,
    obligation_id: str,
    decision: str,
    revision: int,
    note: Optional[str],
    evidence_reference: Optional[str],
) -> Dict[str, Any]:
    """构造一条人工补核审计事件（纯函数；落盘在提交之后，与复核审计同一纪律）。

    复用唯一的审计体系（``append_audit_event`` 的 JSONL），不另开第二套。
    ``note`` / ``evidence_reference`` 是复核人**自己写下**的补核依据——
    它们就是审计要回答的"凭什么这么判"；finding 全文与材料原文仍然不进审计。
    """
    session = state.active_session or state.completed_session or {}
    resource_id = f"{session.get('review_session_id') or ''}:{obligation_id}"
    return {
        "action": action,
        "actor": actor,
        "result": result,
        "resource_type": "review_obligation_decision",
        "resource_id": resource_id,
        "details": {
            "slot_id": state.slot_id,
            "document_version_id": state.analysis.document_version_id,
            "analysis_job_uuid": state.analysis.job_uuid,
            "analysis_basis_token": state.basis_token,
            "review_session_id": str(session.get("review_session_id") or ""),
            "obligation_id": obligation_id,
            "decision": decision,
            "revision": revision,
            "note": note,
            "evidence_reference": evidence_reference,
        },
    }


def _append_audit_best_effort(event: Dict[str, Any]) -> None:
    """把审计事件追加到 JSONL（尽力而为）。

    数据库**已经提交**之后才调用，因此这里失败不能反向把业务结果说成失败
    （那会变成"复核其实成功了，但用户被告知失败"）。失败只记运维错误。
    真要做到"审计与业务同生共死"需要数据库 outbox，属于另一个层面的改造，
    本轮不做（评审意见也明确不要引入完整 event bus）。
    """
    try:
        append_audit_event(**event)
    except Exception:  # noqa: BLE001 - 见上：提交后的审计失败不改业务结论
        logger.exception(
            "Failed to append review audit event %s (business result already committed)",
            str(event.get("action") or ""),
        )


def _iso(value: Any) -> str:
    if value is None:
        return ""
    try:
        return value.isoformat()
    except AttributeError:
        return str(value)


def _opt_iso(value: Any) -> Optional[str]:
    return None if value is None else _iso(value)


def _opt_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


__all__ = [
    "ReviewLifecycleError",
    "ReviewState",
    "evaluate_start_gate",
    "evaluate_completion_gate",
    "load_review_by_slot",
    "load_review_by_job",
    "start_review",
    "complete_review",
    "reopen_review",
    "decide_obligation",
    "invalidate_review",
    "invalidate_reviews_for_analysis_restart",
]
