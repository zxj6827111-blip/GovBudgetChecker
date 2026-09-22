"""复核问题集合：后端对"什么算一条待人工处理的问题"的**唯一**口径。

为什么必须有这个模块
--------------------
审核工作台（``app/lib/uiAdapters.ts`` 的 ``toUiProblems``）把四类来源合成了一个
问题列表：双模式 findings（``ai_findings`` + ``rule_findings``，经 ``merged.merged_ids``
筛选）、legacy ``issues`` 分桶、以及 ``structured_ingest.review_items``。

完成门禁如果只看"正式 finding"，就会出现最危险的一类假完成：

    前端还挂着「结构化识别待复核」
    → 后端却认为问题都处理完了
    → 复核被判定为完成

因此本模块把这三类来源的**身份合成规则**在服务端实现一遍，作为完成门禁与
``issue_workflow_store`` 问题定位（用户点"确认/忽略"时到底改的是哪一条）的
共同依据。前端仍按自己的展示逻辑渲染，但"哪些问题必须有人做过决定"这件事
只有这一个出口。

与前端 ``toUiProblems`` 的逐条对应
----------------------------------
============================  ==================================================
前端                          本模块
============================  ==================================================
``collectDisplayIssues``      ``_display_issues``（merged_ids 优先 → legacy → 全量）
``collectStructuredReviewIssues``  ``_structured_issues``（含 id 缺省时的合成规则）
``dedupeIssuesById``          ``_dedupe_by_id``（保留首次出现，顺序稳定）
``ignored_issue_ids`` 过滤      ``ignored_issue_ids`` 参数
``isProblemDegraded``         ``evidence_guard.is_formal_finding``
============================  ==================================================

"降级问题"为什么不阻塞
----------------------
缺少证据被 evidence guard 降级的条目（``evidence_status =
degraded_missing_evidence``）在界面上**照样显示**（用户有权知道哪条证据不完整），
但不算正式问题——这一点前后端必须一致，否则底部状态条的"待处理"数会比
"审核问题"条数还多，出现口径分裂。因此 ``is_formal=False`` 的条目可以出现在
集合里供展示，但不参与阻塞统计。

失效模式：宁可多要一个人工决定
------------------------------
``ignored_issue_ids``（legacy 忽略机制）里的条目**不出现**在集合里——它们在前端
同样看不到，门禁若仍要求处理，用户会遇到"页面上根本没有这条问题却完不成"的
死局。这是"与工作台可见集合一致"的直接推论，不是绕过门禁。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

from src.schemas.review_lifecycle import (
    PROBLEM_SOURCE_FINDING,
    PROBLEM_SOURCE_STRUCTURED_REVIEW,
)
from src.services.evidence_guard import is_formal_finding

logger = logging.getLogger(__name__)

#: structured review item 缺省 id 的合成前缀（与前端同名字面量）。
STRUCTURED_REVIEW_ID_MARKER = ":structured-review:"


@dataclass(frozen=True)
class ReviewProblem:
    """一条需要人工表态的复核问题。

    ``issue_id`` 是**跨前后端、跨存储**的同一身份：前端 ``Problem.id``、
    ``issue_workflow_store`` 的 ``issue_id``、完成门禁统计的键，三处必须是它。
    """

    issue_id: str
    source: str
    title: str
    severity: str
    is_formal: bool

    @property
    def is_blocking_candidate(self) -> bool:
        """是否参与"待处理/需复核"阻塞统计。

        降级条目（证据不足被改为 ``manual_review``）不参与：它们在界面上是
        "提醒"，不是"待办"。这条判断与前端 ``isProblemDegraded`` 逐字同义。
        """
        return self.is_formal


def _json_value(raw: Any) -> Any:
    """JSONB 列的取值：可能是 dict/list，也可能是 JSON 字符串。

    本仓库没有给 asyncpg 注册 JSON codec，真库取回来是字符串——与
    ``material_detail_query_service._json_value`` 同一手法。
    """
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return raw
        try:
            return json.loads(text)
        except Exception:  # noqa: BLE001 - 解析失败按原值处理，由调用方兜底
            return raw
    return raw


def _json_dict(raw: Any) -> Dict[str, Any]:
    value = _json_value(raw)
    return value if isinstance(value, dict) else {}


def _json_list(raw: Any) -> List[Any]:
    value = _json_value(raw)
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _to_number(value: Any, fallback: float = 0.0) -> float:
    """与前端 ``toNumber`` 同义：``Number(value)``，非有限数退回 ``fallback``。

    必须复刻而不是"能转就转"：前端 ``page`` 走同一个 ``toNumber``，
    两边对 ``"3"`` / ``3`` / ``None`` 的判定必须一致，否则合成出的 issue_id
    会对不上，表现为"页面上有这条问题，门禁却说它没处理完"。
    """
    if isinstance(value, bool):
        return fallback
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return fallback
    if numeric != numeric or numeric in (float("inf"), float("-inf")):
        return fallback
    return numeric


def _issue_id_of(item: Dict[str, Any]) -> str:
    return _text(item.get("id"))


def _dedupe_by_id(items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按 ``id`` 去重，保留首次出现（与前端 ``dedupeIssuesById`` 同义）。

    无 id 的条目一律保留：去重键缺失时合并它们会静默丢掉问题，
    而"丢掉一条待处理问题"正是本轮要消灭的假完成来源。
    """
    deduped: List[Dict[str, Any]] = []
    seen: set = set()
    for item in items:
        issue_id = _issue_id_of(item)
        if issue_id:
            if issue_id in seen:
                continue
            seen.add(issue_id)
        deduped.append(item)
    return deduped


def _legacy_issues(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """legacy ``issues`` 分桶展平（与前端 ``collectLegacyIssues`` 同义）。"""
    issues = result.get("issues")
    if isinstance(issues, dict):
        all_items = issues.get("all")
        if isinstance(all_items, list):
            return [item for item in all_items if isinstance(item, dict)]
        collected: List[Dict[str, Any]] = []
        for key in ("error", "warn", "info"):
            bucket = issues.get(key)
            if isinstance(bucket, list):
                collected.extend(item for item in bucket if isinstance(item, dict))
        return collected
    if isinstance(issues, list):
        return [item for item in issues if isinstance(item, dict)]
    return []


def _display_issues(
    *,
    ai_findings: Any,
    rule_findings: Any,
    merged_result: Any,
    result: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """正式展示用的 finding 集合（与前端 ``collectDisplayIssues`` 同义）。

    优先级是有意的：``merged.merged_ids`` 非空时**只**取被合并进去的条目。
    合并的意义就是"AI 与规则指向同一条问题"，此时还按原始两条展示会把
    一条问题数成两条——问题总数会虚高，而"已处理 N/M"的分母跟着失真。
    """
    ai_items = [item for item in _json_list(ai_findings) if isinstance(item, dict)]
    rule_items = [item for item in _json_list(rule_findings) if isinstance(item, dict)]
    source_issues = _dedupe_by_id([*ai_items, *rule_items])

    merged = _json_dict(merged_result)
    merged_ids = [_text(item) for item in _json_list(merged.get("merged_ids"))]
    merged_ids = [item for item in merged_ids if item]

    if merged_ids and source_issues:
        by_id: Dict[str, Dict[str, Any]] = {}
        for item in source_issues:
            issue_id = _issue_id_of(item)
            if issue_id and issue_id not in by_id:
                by_id[issue_id] = item
        selected: List[Dict[str, Any]] = []
        seen: set = set()
        for merged_id in merged_ids:
            if merged_id in seen:
                continue
            item = by_id.get(merged_id)
            if item is None:
                continue
            seen.add(merged_id)
            selected.append(item)
        if selected:
            return selected

    legacy = _legacy_issues(result or {})
    if legacy:
        return _dedupe_by_id(legacy)
    return source_issues


def _structured_issues(
    *, job_uuid: str, structured_ingest: Any
) -> List[Dict[str, Any]]:
    """结构化入库的待复核项（与前端 ``collectStructuredReviewIssues`` 同义）。"""
    structured = _json_dict(structured_ingest)
    review_items = [
        item for item in _json_list(structured.get("review_items")) if isinstance(item, dict)
    ]

    collected: List[Dict[str, Any]] = []
    for index, item in enumerate(review_items):
        raw_page = _to_number(item.get("page", item.get("page_number")))
        page: Optional[int] = int(raw_page) if raw_page > 0 else None
        table_code = _text(item.get("table_code"))
        item_type = _text(item.get("type")) or "review"

        issue_id = _issue_id_of(item)
        if not issue_id:
            # 与前端逐字同一条合成规则。真数据里 review_items 都带 id
            # （见 structured_ingest_runner._build_review_items），
            # 这条兜底存在只是为了让"两端对同一条 item 得到同一个 id"这件事
            # 不依赖生产者是否恰好写了 id。
            page_token = str(page) if page is not None else "unlocated"
            issue_id = (
                f"{job_uuid}{STRUCTURED_REVIEW_ID_MARKER}{item_type}:"
                f"{table_code or 'document'}:{page_token}:{index + 1}"
            )

        collected.append(
            {
                "id": issue_id,
                "source": PROBLEM_SOURCE_STRUCTURED_REVIEW,
                "rule_id": f"STRUCTURED-{item_type.upper()}",
                "severity": _text(item.get("severity")) or "manual_review",
                "table_code": table_code,
                "page": page,
                "title": (
                    f"结构化识别待复核：{table_code}"
                    if table_code
                    else "结构化识别结果待复核"
                ),
                "message": _text(item.get("message"))
                or "该项材料识别结果需要人工确认后再作为审校依据。",
            }
        )
    return collected


def collect_review_problems(
    *,
    job_uuid: str,
    ai_findings: Any = None,
    rule_findings: Any = None,
    merged_result: Any = None,
    structured_ingest: Any = None,
    result: Optional[Dict[str, Any]] = None,
    ignored_issue_ids: Iterable[str] = (),
) -> List[ReviewProblem]:
    """合成当前分析下的复核问题集合（顺序稳定，与前端展示顺序同源）。

    参数里的 ``result`` 只在 legacy ``issues`` 分桶路径上用到；双模式路径下
    ``ai_findings`` / ``rule_findings`` / ``merged_result`` 已经足够。
    """
    display = _display_issues(
        ai_findings=ai_findings,
        rule_findings=rule_findings,
        merged_result=merged_result,
        result=result,
    )
    structured = _structured_issues(job_uuid=job_uuid, structured_ingest=structured_ingest)
    combined = _dedupe_by_id([*display, *structured])

    ignored = {_text(item) for item in ignored_issue_ids if _text(item)}

    problems: List[ReviewProblem] = []
    for index, item in enumerate(combined):
        issue_id = _issue_id_of(item) or f"{job_uuid}-issue-{index + 1}"
        if issue_id in ignored:
            # 与工作台可见集合保持一致：页面看不到的条目不参与门禁，
            # 否则会出现"页面上没有这条问题，却永远完不成复核"的死局。
            continue
        structured_item = _text(item.get("source")) == PROBLEM_SOURCE_STRUCTURED_REVIEW
        problems.append(
            ReviewProblem(
                issue_id=issue_id,
                source=(
                    PROBLEM_SOURCE_STRUCTURED_REVIEW
                    if structured_item
                    else PROBLEM_SOURCE_FINDING
                ),
                title=_text(item.get("title")) or _text(item.get("message")),
                severity=_text(item.get("severity")),
                # 结构化待复核项没有 evidence_status，恒为正式项；
                # finding 是否正式由 evidence guard 唯一决定。
                is_formal=True if structured_item else is_formal_finding(item),
            )
        )
    return problems


def build_issue_counts(
    problems: Sequence[ReviewProblem], decisions: Dict[str, str]
) -> Dict[str, int]:
    """按工作流决定统计各状态条数（写入 ``review_result`` 的问题快照）。

    ``total`` 是**正式问题**条数，恒等于五桶之和（降级条目不计入任何一桶：
    它们既不是"待处理"，也不是"已确认/已忽略"——这一点必须与底部状态条一致，
    否则同一次复核里会出现两个不同的问题总数）。

    未在 ``decisions`` 里出现的问题按 ``pending`` 计——**没有记录不等于已完成**
    是这一块的硬约束：数据库没有行只说明没有人表过态。
    """
    counts: Dict[str, int] = {
        "total": 0,
        "confirmed": 0,
        "no_issue": 0,
        "in_package": 0,
        "pending": 0,
        "needs_review": 0,
    }
    for problem in problems:
        if not problem.is_blocking_candidate:
            continue
        status = _text(decisions.get(problem.issue_id)) or "pending"
        if status not in counts:
            # 未知状态码一律按待处理处理：把不认识的值当成"已处理"，
            # 等于让一个拼错的状态码静默放行复核。
            status = "pending"
        counts[status] += 1
        counts["total"] += 1
    return counts


__all__ = [
    "ReviewProblem",
    "STRUCTURED_REVIEW_ID_MARKER",
    "collect_review_problems",
    "build_issue_counts",
]
