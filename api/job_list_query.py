"""任务列表的服务端筛选、排序、分页与汇总（处理队列、部门报告、任务历史、工作台、质量管理、侧栏角标）。

此前这些计算全在浏览器里做：每个页面都要先拉完整的 /api/jobs（277 个任务约 2.3MB）。
口径逐条对应前端：

- 状态归一：app/lib/uiAdapters.ts ``normalizeUiTaskStatus``；
- 问题数：同文件 ``getDisplayIssueTotal``；
- 队列筛选与排序：app/app/components/queue/queuePageAdapters.ts 与
  workspace/workbenchAdapters.ts ``filterWorkbenchQueue``；
- 报告的年度/类型分桶与组织范围：reports/departmentReportAdapters.ts；
- 轮询判定：app/lib/jobPolling.ts ``resolvePollingDecision``；
- 任务历史的终态范围：history/historyPageAdapters.ts ``filterHistoryJobs``；
- 工作台总览：workspace/workbenchCockpitAdapters.ts（进度、部门覆盖、页面覆盖率、趋势）；
- 质量管理：workspace/workbenchAdapters.ts ``aggregatePageCoverage`` 与
  quality/qualityAdapters.ts（失败阶段、done 任务覆盖率、未关联组织）。

后端未升级时前端仍用上述函数本地计算，两边改动必须同步。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Tuple

UI_STATUSES = ("analyzing", "review_required", "pending_analysis", "failed", "completed")
STATUS_FILTERS = frozenset(("all",) + UI_STATUSES)
KIND_FILTERS = frozenset(("all", "budget", "final", "unknown"))
STAGE_KEYS = frozenset(("upload", "pdf_parse", "metadata_recognition", "rule_ai_analysis", "quality_gate"))
STAGE_FILTERS = frozenset({"all", "unknown"}) | STAGE_KEYS
SORT_KEYS = frozenset(("updated_desc", "issues_desc", "elapsed_desc"))
# 已上传、分析未启动的静止状态：后端没有在干活，轮询放慢而不是按"运行中"处理。
STATIC_WAITING_RAW_STATUSES = frozenset(("uploaded",))
# 失败阶段的展示顺序（同 qualityAdapters.FAILURE_STAGE_ORDER），无法归因的排最后。
FAILURE_STAGE_ORDER = ("upload", "pdf_parse", "metadata_recognition", "rule_ai_analysis", "quality_gate")
# 页面覆盖率参考阈值，同前端 PAGE_COVERAGE_REFERENCE / PAGE_COVERAGE_MIN_RATIO。
DEFAULT_COVERAGE_MIN = 0.8


def normalize_ui_task_status(value: Any) -> str:
    normalized = str(value if value is not None else "").strip().lower()
    if normalized in {"review_required", "review", "needs_review"}:
        return "review_required"
    if normalized in {"uploaded", "pending_analysis"}:
        return "pending_analysis"
    if normalized in {"done", "completed", "complete", "success", "succeeded", "degraded"}:
        return "completed"
    if normalized in {"failed", "error", "failure", "cancelled", "canceled"}:
        return "failed"
    return "analyzing"


def is_ui_task_finished(status: str) -> bool:
    return status in {"completed", "review_required", "failed"}


def _finite_number(value: Any) -> Optional[float]:
    """JS ``Number(value)`` 的有限值；null→0、空串→0、布尔→0/1 与前端一致。"""
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return 0.0
        try:
            number = float(text)
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _is_finite_number_value(value: Any) -> bool:
    """JS ``typeof value === "number" && Number.isFinite(value)``。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


# 区分"字段缺失"（JS undefined → 回退值）与"字段为 null"（JS Number(null) === 0）。
_MISSING = object()


def _to_number(value: Any, fallback: float = 0.0) -> float:
    if value is _MISSING:
        return fallback
    number = _finite_number(value)
    return fallback if number is None else number


def display_issue_total(job: Mapping[str, Any]) -> float:
    if _is_finite_number_value(job.get("review_issue_total")) or _is_finite_number_value(job.get("review_candidate_total")):
        return _to_number(job.get("review_issue_total", _MISSING)) + _to_number(job.get("review_candidate_total", _MISSING))
    merged = _finite_number(job["merged_issue_total"]) if "merged_issue_total" in job else None
    rule_and_ai = merged if merged is not None else _to_number(job.get("issue_total", _MISSING))
    return rule_and_ai + _to_number(job.get("review_item_count", _MISSING))


def stage_key(job: Mapping[str, Any]) -> str:
    raw = job.get("stage_progress")
    if not isinstance(raw, Mapping):
        return "unknown"
    phase = raw.get("phase")
    return phase if isinstance(phase, str) and phase in STAGE_KEYS else "unknown"


def _js_text(value: Any) -> str:
    """``String(value ?? "")``：整数值的浮点数按 JS 显示为不带小数点的整数。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return str(int(value))
    return str(value)


def report_year_bucket(job: Mapping[str, Any]) -> str:
    """报告页年度分桶：正整数年份，否则归入"年度待确认"。"""
    year = job.get("report_year")
    if isinstance(year, bool) or not isinstance(year, (int, float)):
        return "unknown"
    if not math.isfinite(float(year)) or not float(year).is_integer() or year <= 0:
        return "unknown"
    return str(int(year))


def report_kind_bucket(job: Mapping[str, Any]) -> str:
    kind = job.get("report_kind")
    return kind if kind in {"budget", "final"} else "unknown"


def can_run_job(job: Mapping[str, Any]) -> bool:
    """与 queueBatchActions.canRunQueueJob 一致：有 ID 且不在分析中。"""
    return bool(str(job.get("job_id") or "").strip()) and normalize_ui_task_status(job.get("status")) != "analyzing"


@dataclass(frozen=True)
class JobListFilters:
    keyword: str = ""
    status: str = "all"
    # "" 不筛；"unresolved" = report_year 缺失（队列）；"unknown" = 非正整数（报告页）；其他为年份。
    year: str = ""
    kind: str = "all"
    stage: str = "all"
    # None 表示不按组织筛选；unassigned=True 只要未关联组织的任务。
    organization_ids: Optional[FrozenSet[str]] = None
    unassigned: bool = False
    # 只要已结束的任务（任务历史）。
    finished_only: bool = False
    # 只要页面覆盖率低于该值的任务（工作台"页面覆盖率不足"明细）。
    coverage_below: Optional[float] = None


def matches_organization(job: Mapping[str, Any], filters: JobListFilters) -> bool:
    org_id = _js_text(job.get("organization_id"))
    if filters.unassigned:
        return not org_id
    if filters.organization_ids is None:
        return True
    return org_id in filters.organization_ids


def matches_filters(job: Mapping[str, Any], filters: JobListFilters) -> bool:
    keyword = filters.keyword.strip().lower()
    if keyword:
        haystack = " ".join(
            _js_text(job.get(key)).lower() for key in ("filename", "organization_name", "job_id")
        )
        if keyword not in haystack:
            return False
    if filters.status != "all" and normalize_ui_task_status(job.get("status")) != filters.status:
        return False
    if filters.year:
        if filters.year == "unresolved":
            if job.get("report_year") is not None:
                return False
        elif filters.year == "unknown":
            if report_year_bucket(job) != "unknown":
                return False
        elif _js_text(job.get("report_year")) != filters.year:
            return False
    if filters.kind != "all" and report_kind_bucket(job) != filters.kind:
        return False
    if filters.stage != "all" and stage_key(job) != filters.stage:
        return False
    if filters.finished_only and not is_ui_task_finished(normalize_ui_task_status(job.get("status"))):
        return False
    if filters.coverage_below is not None and not is_low_coverage(job, filters.coverage_below):
        return False
    return matches_organization(job, filters)


def filter_jobs(jobs: Iterable[Mapping[str, Any]], filters: JobListFilters) -> List[Mapping[str, Any]]:
    return [job for job in jobs if matches_filters(job, filters)]


def _elapsed_sort_value(job: Mapping[str, Any]) -> float:
    value = job.get("elapsed_ms")
    if _is_finite_number_value(value) and value >= 0:
        return float(value)
    return -1.0  # 未知耗时排在最后，不冒充 0


def sort_jobs(jobs: Sequence[Mapping[str, Any]], sort_key: str) -> List[Mapping[str, Any]]:
    """稳定排序；updated_desc 保持传入顺序（列表接口已按更新时间降序）。"""
    if sort_key == "issues_desc":
        return sorted(jobs, key=display_issue_total, reverse=True)
    if sort_key == "elapsed_desc":
        return sorted(jobs, key=_elapsed_sort_value, reverse=True)
    return list(jobs)


def paginate(items: Sequence[Any], page: int, page_size: int) -> Tuple[List[Any], int, int]:
    """返回 (当页条目, 收敛后的页码, 总页数)。空结果总页数为 0、页码为 1。"""
    total = len(items)
    if page_size <= 0 or total == 0:
        return [], 1, 0
    page_count = math.ceil(total / page_size)
    current = min(max(1, page), page_count)
    start = (current - 1) * page_size
    return list(items[start : start + page_size]), current, page_count


def collect_years(jobs: Iterable[Mapping[str, Any]]) -> Tuple[List[int], bool]:
    """可选年份（降序）与是否存在年度待确认的任务。"""
    years = set()
    has_unknown = False
    for job in jobs:
        bucket = report_year_bucket(job)
        if bucket == "unknown":
            has_unknown = True
        else:
            years.add(int(bucket))
    return sorted(years, reverse=True), has_unknown


def status_counts(jobs: Iterable[Mapping[str, Any]]) -> Dict[str, int]:
    counts = {status: 0 for status in UI_STATUSES}
    for job in jobs:
        counts[normalize_ui_task_status(job.get("status"))] += 1
    return counts


def polling_activity(jobs: Iterable[Mapping[str, Any]]) -> Dict[str, int]:
    """未结束任务里"在跑"与"静止等待"的数量，前端据此决定轮询频率。"""
    running = 0
    waiting = 0
    for job in jobs:
        status = normalize_ui_task_status(job.get("status"))
        if is_ui_task_finished(status):
            continue
        raw = str(job.get("status") if job.get("status") is not None else "").strip().lower()
        if status == "pending_analysis" or raw in STATIC_WAITING_RAW_STATUSES:
            waiting += 1
        else:
            running += 1
    return {"running": running, "waiting": waiting}


def organization_report_counts(jobs: Iterable[Mapping[str, Any]]) -> Dict[str, int]:
    """各组织本级的报告数；上级合计由前端按可见组织树累加。"""
    counts: Dict[str, int] = {}
    for job in jobs:
        org_id = _js_text(job.get("organization_id"))
        if org_id:
            counts[org_id] = counts.get(org_id, 0) + 1
    return counts


def descendant_organization_ids(organizations: Iterable[Any], root_id: str) -> FrozenSet[str]:
    """root 及其全部下级组织 ID（按 parent_id 关系，防环）。"""
    children: Dict[str, List[str]] = {}
    for org in organizations:
        parent_id = getattr(org, "parent_id", None)
        org_id = getattr(org, "id", None)
        if parent_id and org_id:
            children.setdefault(str(parent_id), []).append(str(org_id))
    result = {root_id}
    stack = [root_id]
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in result:
                result.add(child)
                stack.append(child)
    return frozenset(result)


def _valid_coverage(job: Mapping[str, Any]) -> Optional[float]:
    """0～1 之间的有限数字（cockpitPageCoverage 口径），否则 None。"""
    value = job.get("page_coverage")
    if _is_finite_number_value(value) and 0 <= value <= 1:
        return float(value)
    return None


def _numeric_coverage(job: Mapping[str, Any]) -> Optional[float]:
    """字段存在且 JS ``Number(raw)`` 为有限值（aggregatePageCoverage 口径），否则 None。"""
    value = job.get("page_coverage")
    if value is None:
        return None
    return _finite_number(value)


def is_low_coverage(job: Mapping[str, Any], minimum: float) -> bool:
    """lowCoverageJobs 口径：确有覆盖率数字且低于阈值；缺字段不算覆盖率不足。"""
    value = job.get("page_coverage")
    return _is_finite_number_value(value) and 0 <= value < minimum


def _coverage_aggregate(values: Sequence[Optional[float]]) -> Dict[str, Any]:
    present = [value for value in values if value is not None]
    return {
        "average": sum(present) / len(present) if present else None,
        "sample_size": len(present),
        "excluded_count": len(values) - len(present),
    }


def done_coverage_stats(jobs: Iterable[Mapping[str, Any]], minimum: float) -> Optional[Dict[str, int]]:
    """computeDoneCoverageStats：只看 done 且带覆盖率的任务；无样本返回 None（不是"全部达标"）。"""
    sample_size = 0
    low_coverage_count = 0
    for job in jobs:
        if normalize_ui_task_status(job.get("status")) != "completed":
            continue
        value = _numeric_coverage(job)
        if value is None:
            continue
        sample_size += 1
        if value < minimum:
            low_coverage_count += 1
    if sample_size == 0:
        return None
    return {"sample_size": sample_size, "low_coverage_count": low_coverage_count}


def failure_stage_distribution(jobs: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """deriveFailureStageDistribution：只统计当前失败的任务，按数量降序、阶段顺序次之。"""
    counts: Dict[str, int] = {}
    labels: Dict[str, str] = {}
    for job in jobs:
        if normalize_ui_task_status(job.get("status")) != "failed":
            continue
        failed_at = job.get("stage_failed_at")
        failed_at = failed_at if isinstance(failed_at, Mapping) else {}
        phase = failed_at.get("phase")
        if isinstance(phase, str) and phase in FAILURE_STAGE_ORDER:
            label = failed_at.get("phase_label")
            counts[phase] = counts.get(phase, 0) + 1
            labels[phase] = phase if label is None else _js_text(label)
        else:
            counts["unattributed"] = counts.get("unattributed", 0) + 1
            labels["unattributed"] = "未归因"

    def order(phase: str) -> int:
        return FAILURE_STAGE_ORDER.index(phase) if phase in FAILURE_STAGE_ORDER else len(FAILURE_STAGE_ORDER)

    buckets = [{"phase": phase, "label": labels[phase], "count": count} for phase, count in counts.items()]
    buckets.sort(key=lambda bucket: (-bucket["count"], order(bucket["phase"])))
    return buckets


def unassociated_count(jobs: Iterable[Mapping[str, Any]]) -> int:
    """computeUnassociatedOrgRatio 的分子：organization_id 去空白后为空。"""
    return sum(1 for job in jobs if not _js_text(job.get("organization_id")).strip())


def event_timestamp_ms(value: Any) -> Optional[float]:
    """cockpitTimestamp：正的有限数字，大于 1e12 视为毫秒，否则按秒换算。"""
    if not _is_finite_number_value(value) or value <= 0:
        return None
    return float(value) if value > 1_000_000_000_000 else float(value) * 1000


def _local_date(timestamp_ms: float, tz: Optional[tzinfo]) -> Optional[date]:
    try:
        return datetime.fromtimestamp(timestamp_ms / 1000, tz).date()
    except (OverflowError, OSError, ValueError):
        return None  # JS 侧同样得到无效日期，不落入任何一天


def daily_trend(
    jobs: Iterable[Mapping[str, Any]], *, days: int, tz: Optional[tzinfo], now_ms: float
) -> Dict[str, Any]:
    """buildCockpitTrend：按浏览器所在时区的自然日统计新增与结束分析。

    updated_ts 可能因元数据编辑而变化，不能拿它冒充完成时间；只用 created_ts 与
    analysis_finished_ts。缺时间的任务单独计数，未来时间不计入。
    """
    today = _local_date(now_ms, tz) or datetime.now(tz).date()
    points = [
        {"date": (today - timedelta(days=days - 1 - index)).isoformat(), "uploaded": 0, "analyzed": 0}
        for index in range(days)
    ]
    by_date = {point["date"]: point for point in points}
    missing_upload_times = 0
    missing_analysis_times = 0

    def bucket(timestamp_ms: float) -> Optional[Dict[str, Any]]:
        local = _local_date(timestamp_ms, tz)
        return by_date.get(local.isoformat()) if local is not None else None

    for job in jobs:
        created = event_timestamp_ms(job.get("created_ts"))
        if created is None:
            missing_upload_times += 1
        elif created <= now_ms:
            point = bucket(created)
            if point is not None:
                point["uploaded"] += 1
        if normalize_ui_task_status(job.get("status")) not in {"completed", "review_required"}:
            continue
        finished = event_timestamp_ms(job.get("analysis_finished_ts"))
        if finished is None:
            missing_analysis_times += 1
        elif finished <= now_ms:
            point = bucket(finished)
            if point is not None:
                point["analyzed"] += 1
    return {
        "points": points,
        "missing_upload_times": missing_upload_times,
        "missing_analysis_times": missing_analysis_times,
    }


def build_counts_view(jobs: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    return {"total": len(jobs), "status_counts": status_counts(jobs), "activity": polling_activity(jobs)}


def build_page_view(
    jobs: Sequence[Mapping[str, Any]],
    filters: JobListFilters,
    *,
    sort_key: str,
    page: int,
    page_size: int,
    include_runnable: bool = False,
    include_organization_counts: bool = False,
) -> Dict[str, Any]:
    filtered = filter_jobs(jobs, filters)
    items, current, page_count = paginate(sort_jobs(filtered, sort_key), page, page_size)
    # 年份选项只受组织范围影响，不随年份/类型/关键词筛选收窄，否则选中后其他年份会消失。
    years, has_unknown_year = collect_years(job for job in jobs if matches_organization(job, filters))
    view: Dict[str, Any] = {
        "items": items,
        "total": len(filtered),
        "page": current,
        "page_size": page_size,
        "page_count": page_count,
        "years": years,
        "has_unknown_year": has_unknown_year,
        # total 是筛选后的条数；job_total 与两项汇总覆盖全部可见任务（角标、轮询用）。
        "job_total": len(jobs),
        "status_counts": status_counts(jobs),
        "activity": polling_activity(jobs),
    }
    if include_runnable:
        # "全选筛选结果"与批量运行需要跨页的任务；只给 ID、文件名、状态，不再回传全量摘要。
        view["runnable"] = [
            {"job_id": job.get("job_id"), "filename": job.get("filename"), "status": job.get("status")}
            for job in filtered
            if can_run_job(job)
        ]
    if include_organization_counts:
        view["organization_report_counts"] = organization_report_counts(jobs)
    return view


def build_overview_view(
    jobs: Sequence[Mapping[str, Any]],
    *,
    coverage_min: float,
    trend_days: int,
    tz: Optional[tzinfo],
    now_ms: float,
) -> Dict[str, Any]:
    """工作台总览与质量管理页的全部任务汇总；明细列表另走 view=page。"""
    return {
        **build_counts_view(jobs),
        "organization_report_counts": organization_report_counts(jobs),
        "unassociated_count": unassociated_count(jobs),
        # 两种覆盖率口径分别对应工作台（只认 0～1）与质量管理页（任何有限数值）。
        "page_coverage": _coverage_aggregate([_valid_coverage(job) for job in jobs]),
        "page_coverage_numeric": _coverage_aggregate([_numeric_coverage(job) for job in jobs]),
        "coverage_min": coverage_min,
        "low_coverage_count": sum(1 for job in jobs if is_low_coverage(job, coverage_min)),
        "done_coverage": done_coverage_stats(jobs, coverage_min),
        "failure_stages": failure_stage_distribution(jobs),
        "trend": daily_trend(jobs, days=trend_days, tz=tz, now_ms=now_ms),
    }


def parse_choice(value: Optional[str], allowed: FrozenSet[str], default: str) -> str:
    text = str(value or "").strip()
    return text if text in allowed else default


def parse_positive_int(value: Optional[str], default: int, *, maximum: int) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return min(max(1, number), maximum)


def parse_ratio(value: Optional[str], default: Optional[float]) -> Optional[float]:
    """(0, 1] 之间的比例，否则取默认值。"""
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) and 0 < number <= 1 else default
