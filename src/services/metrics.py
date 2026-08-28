"""运行时关键指标采集（缺口 B-06）。

设计取舍
--------
指标来源统一是 `UPLOAD_DIR` 下既有的任务产物（`status.json`）与 worker 心跳目录，
**不引入新的存储、不额外写文件**。理由：

1. 任务产物本来就是本系统的事实源（数据库不可用时也能工作），从它算指标
   不会出现"指标说 A、产物说 B"的两套事实；
2. 心跳目录（`UPLOAD_DIR/.worker-heartbeats`）是 `api/queue_runtime.py` 已有的
   队列存活判据，直接复用，避免另造一套队列观测口径。

为什么选 `/metrics` 端点而不是纯结构化日志聚合
--------------------------------------------
结构化日志聚合（Task 10 已接线）只解决"事件可检索"，要拿到"当前积压多少、
review_required 比例多少"这类即时状态，仍需要外部日志管道先落地并写查询，
本轮无法自证。端点方案可以在本仓库内被测试直接断言，也能被 Prometheus 抓取，
所以两者是叠加关系：明细看日志，态势看端点。

鉴权：端点默认要求管理员会话（沿用 `/ready?details=true` 的先例），
或配置独立的抓取令牌 `METRICS_API_TOKEN`。绝不无鉴权暴露内部指标。
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.schemas.issues import (
    ACTIVE_JOB_STATUSES,
    AnalysisConclusion,
    JobStatus,
    normalize_job_status,
)
from src.services.evidence_guard import count_formal_findings
from src.utils.report_year import parse_report_year

#: 心跳文件目录名，与 api/queue_runtime.has_recent_worker_heartbeat 保持一致
HEARTBEAT_DIRNAME = ".worker-heartbeats"

#: 指标缓存默认 TTL（秒）。指标端点会扫描全部任务目录，必须防止被高频抓爆。
_DEFAULT_CACHE_TTL_SECONDS = 15.0

#: 单次采集扫描的任务目录上限，避免产物膨胀后拖垮端点
_DEFAULT_MAX_JOBS = 5000

_cache: Dict[str, Any] = {"key": None, "ts": 0.0, "payload": None}


def _env_float(name: str, default: float) -> float:
    try:
        value = float(str(os.getenv(name, "")).strip())
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def _env_int(name: str, default: int) -> int:
    try:
        value = int(str(os.getenv(name, "")).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _basename(path_text: Any) -> str:
    """跨平台取文件名。

    不能用 `Path(...).name`：产物里可能存着 Windows 风格路径，
    在 Linux 上没有路径分隔符会把整串当成文件名。
    """
    text = str(path_text or "").strip()
    if not text:
        return ""
    return re.split(r"[\\/]", text)[-1]


def _as_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _ratio(count: int, total: int) -> float:
    return round(count / total, 4) if total else 0.0


def _percentile(values: List[float], fraction: float) -> Optional[float]:
    """最近邻插值分位数。样本量小的时候不做插值，避免造出不存在的数值。"""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return round(ordered[index], 2)


def _summarize_durations(values: List[float]) -> Dict[str, Any]:
    return {
        "count": len(values),
        "mean_ms": round(sum(values) / len(values), 2) if values else None,
        "p50_ms": _percentile(values, 0.5),
        "p95_ms": _percentile(values, 0.95),
        "max_ms": round(max(values), 2) if values else None,
    }


def report_id_uniqueness(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """report_id 唯一性检查（P0-09 的回归观测口，也是 B-06 的冲突计数来源）。

    判定标准：同一个 report_id 下出现了多个不同 checksum，说明不同原件被并到了
    同一份报告身份上。checksum 缺失的任务无法参与判定，单独计数如实说明。

    离线回放脚本与运行时指标端点共用本函数，保证"冲突数"只有一个口径。
    """
    grouped: Dict[str, Dict[str, Any]] = {}
    missing_checksum = 0
    for record in records:
        report_id = record.get("report_id")
        if not report_id:
            continue
        entry = grouped.setdefault(
            str(report_id), {"job_ids": [], "checksums": set(), "jobs_without_checksum": 0}
        )
        entry["job_ids"].append(record["job_id"])
        checksum = record.get("checksum")
        if checksum:
            entry["checksums"].add(str(checksum))
        else:
            entry["jobs_without_checksum"] += 1
            missing_checksum += 1

    collisions = [
        {
            "report_id": report_id,
            "job_ids": sorted(entry["job_ids"]),
            "distinct_checksums": sorted(entry["checksums"]),
        }
        for report_id, entry in sorted(grouped.items())
        if len(entry["checksums"]) > 1
    ]

    return {
        "jobs_with_report_id": sum(len(entry["job_ids"]) for entry in grouped.values()),
        "distinct_report_ids": len(grouped),
        "collision_count": len(collisions),
        "collisions": collisions,
        "jobs_without_checksum": missing_checksum,
        "unique": not collisions,
    }


def _result_meta(payload: Dict[str, Any]) -> Dict[str, Any]:
    result = payload.get("result")
    if not isinstance(result, dict):
        return {}
    meta = result.get("meta")
    return meta if isinstance(meta, dict) else {}


def _resolve_report_id(payload: Dict[str, Any]) -> Optional[str]:
    structured = payload.get("structured_ingest")
    if not isinstance(structured, dict):
        meta = _result_meta(payload)
        candidate = meta.get("structured_ingest")
        structured = candidate if isinstance(candidate, dict) else None
    if not isinstance(structured, dict):
        return None
    ps_sync = structured.get("ps_sync")
    if not isinstance(ps_sync, dict):
        return None
    return str(ps_sync.get("report_id") or "").strip() or None


def collect_job_metric_record(job_dir: Path) -> Optional[Dict[str, Any]]:
    """从单个任务目录提取指标所需的最小字段集（只读，不改任何产物）。"""
    status_file = job_dir / "status.json"
    if not status_file.is_file():
        return None
    payload = _read_json(status_file)
    if not payload:
        return None

    meta = _result_meta(payload)
    elapsed = meta.get("elapsed_ms")
    elapsed = elapsed if isinstance(elapsed, dict) else {}
    started_at = _as_float(meta.get("started_at"))
    finished_at = _as_float(meta.get("finished_at"))
    total_ms: Optional[float] = None
    if started_at is not None and finished_at is not None and finished_at >= started_at:
        total_ms = (finished_at - started_at) * 1000.0

    provider_stats = meta.get("provider_stats")
    provider_fallbacks = 0
    if isinstance(provider_stats, list):
        provider_fallbacks = sum(
            1 for item in provider_stats if isinstance(item, dict) and item.get("fell_back")
        )

    evidence = meta.get("evidence_completeness")
    evidence = evidence if isinstance(evidence, dict) else {}

    raw_status = str(payload.get("status") or "")
    result = payload.get("result") if isinstance(payload.get("result"), dict) else None

    # 证据完整率分子分母（UI 重建第四批 Task 7.2 补充）：
    # - total/complete 缺失时保持 None（历史任务没有 evidence_completeness 留痕），
    #   与 degraded_count 的"缺失按 0"不同——计数可以缺省为 0，但"率"的分母
    #   绝不能用 0 冒充，否则空样本会被算成 100% 完整。
    # - has_evidence_field 单独记录，供聚合层报告真实样本量（带留痕的任务数），
    #   避免"分母为 0 显示 —"时用户误以为扫描了 0 个任务。
    has_evidence_field = isinstance(meta.get("evidence_completeness"), dict)

    return {
        "job_id": str(payload.get("job_id") or job_dir.name),
        "filename": _basename(payload.get("filename") or payload.get("saved_path")),
        "checksum": str(payload.get("checksum") or "").strip() or None,
        "raw_status": raw_status,
        "status": normalize_job_status(raw_status),
        "quality_status": str(payload.get("quality_status") or "").strip() or None,
        "analysis_conclusion": str(payload.get("analysis_conclusion") or "").strip() or None,
        "report_kind": str(
            payload.get("report_kind") or meta.get("report_kind") or ""
        ).strip().lower()
        or "unknown",
        "report_year": parse_report_year(payload.get("report_year") or payload.get("fiscal_year")),
        "updated_ts": _as_float(payload.get("ts")) or 0.0,
        "use_ai_assist": bool(payload.get("use_ai_assist", meta.get("use_ai_assist", False))),
        "rule_stage_ms": _as_float(elapsed.get("rule")),
        "ai_stage_ms": _as_float(elapsed.get("ai")),
        "total_stage_ms": total_ms,
        "provider_fallbacks": provider_fallbacks,
        "evidence_degraded_count": _as_int(evidence.get("degraded_count")) or 0,
        "evidence_total": _as_int(evidence.get("total")) if has_evidence_field else None,
        "evidence_complete": _as_int(evidence.get("complete")) if has_evidence_field else None,
        "has_evidence_field": has_evidence_field,
        "formal_issue_total": count_formal_findings(result) if result else 0,
        "report_id": _resolve_report_id(payload),
    }


def collect_worker_heartbeats(
    uploads_root: Path,
    *,
    now: Optional[float] = None,
    max_age_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    """读取 worker 心跳，得出存活 worker 数与队列自报的积压量。"""
    reference = time.time() if now is None else now
    if max_age_seconds is None:
        max_age_seconds = max(
            5.0, _env_float("JOB_QUEUE_HEARTBEAT_MAX_AGE_SECONDS", 15.0)
        )

    heartbeat_dir = uploads_root / HEARTBEAT_DIRNAME
    live = 0
    stale = 0
    reported_active = 0
    reported_queued = 0
    oldest_age: Optional[float] = None

    try:
        paths = sorted(heartbeat_dir.glob("*.json"))
    except OSError:
        paths = []

    for path in paths:
        payload = _read_json(path)
        ts = _as_float(payload.get("ts"))
        if ts is None:
            try:
                ts = path.stat().st_mtime
            except OSError:
                continue
        age = max(0.0, reference - ts)
        if oldest_age is None or age > oldest_age:
            oldest_age = age
        if age <= max_age_seconds:
            live += 1
            reported_active += _as_int(payload.get("active_jobs")) or 0
            reported_queued += _as_int(payload.get("queued_jobs")) or 0
        else:
            stale += 1

    return {
        "live_workers": live,
        "stale_heartbeats": stale,
        "reported_active_jobs": reported_active,
        "reported_queued_jobs": reported_queued,
        "oldest_heartbeat_age_seconds": round(oldest_age, 2) if oldest_age is not None else None,
        "heartbeat_max_age_seconds": max_age_seconds,
    }


def _iter_job_dirs(uploads_root: Path, limit: int) -> List[Path]:
    if not uploads_root.is_dir():
        return []
    try:
        children = sorted(
            (
                child
                for child in uploads_root.iterdir()
                if child.is_dir() and not child.name.startswith(".")
            ),
            key=lambda item: item.name,
        )
    except OSError:
        return []
    return children[:limit]


def collect_metrics(
    uploads_root: Path,
    *,
    now: Optional[float] = None,
    max_jobs: Optional[int] = None,
) -> Dict[str, Any]:
    """采集全部关键指标。无数据时返回结构完整、计数为 0 的骨架，不返回 None。"""
    reference = time.time() if now is None else now
    limit = max_jobs if max_jobs is not None else _env_int("METRICS_MAX_JOBS", _DEFAULT_MAX_JOBS)

    records: List[Dict[str, Any]] = []
    skipped = 0
    for job_dir in _iter_job_dirs(uploads_root, limit):
        record = collect_job_metric_record(job_dir)
        if record is None:
            skipped += 1
            continue
        records.append(record)

    total = len(records)

    # ---- 队列积压 ----
    queued_jobs = [
        record for record in records if (record.get("raw_status") or "").lower() == "queued"
    ]
    active_jobs = [
        record
        for record in records
        if (record.get("status") or "") in ACTIVE_JOB_STATUSES
    ]
    processing_jobs = [
        record
        for record in records
        if (record.get("status") or "") == JobStatus.PROCESSING.value
    ]
    oldest_queued_age: Optional[float] = None
    for record in queued_jobs:
        ts = _as_float(record.get("updated_ts")) or 0.0
        if ts <= 0:
            continue
        age = max(0.0, reference - ts)
        if oldest_queued_age is None or age > oldest_queued_age:
            oldest_queued_age = age

    heartbeats = collect_worker_heartbeats(uploads_root, now=reference)

    # ---- 阶段耗时 ----
    stage_durations = {
        "rule": [
            value
            for value in (record.get("rule_stage_ms") for record in records)
            if isinstance(value, (int, float)) and value > 0
        ],
        "ai": [
            value
            for value in (record.get("ai_stage_ms") for record in records)
            if isinstance(value, (int, float)) and value > 0
        ],
        "total": [
            value
            for value in (record.get("total_stage_ms") for record in records)
            if isinstance(value, (int, float)) and value > 0
        ],
    }

    # ---- AI 失败率 ----
    # 判据：请求了 AI 辅助的任务里，出现 provider 回退或质量状态为 degraded 的比例。
    # 两者取或，是因为 degraded 覆盖"AI 整体不可用"，provider_stats 覆盖"单次调用回退"。
    ai_requested = [record for record in records if record.get("use_ai_assist")]
    ai_failed = [
        record
        for record in ai_requested
        if int(record.get("provider_fallbacks") or 0) > 0
        or (record.get("quality_status") or "") == "degraded"
        or (record.get("status") or "") == JobStatus.DEGRADED.value
    ]

    # ---- 结论/口径类比例 ----
    unknown_kind = [
        record for record in records if (record.get("report_kind") or "unknown") in {"", "unknown"}
    ]
    review_required = [
        record
        for record in records
        if (record.get("status") or "") == JobStatus.REVIEW_REQUIRED.value
        or (record.get("analysis_conclusion") or "") == AnalysisConclusion.INCOMPLETE.value
    ]
    unresolved_year = [record for record in records if record.get("report_year") is None]
    error_jobs = [
        record for record in records if (record.get("status") or "") == JobStatus.ERROR.value
    ]

    # ---- 证据完整率（UI 重建第四批 Task 7.2 补充）----
    # 口径与 scripts/replay_analysis.py 的 evidence_completeness 一致：
    # 分母 = 全部 finding 条数（含降级与规则告警条目，取自各任务
    # result.meta.evidence_completeness.total 的累加），分子 = 证据完整条数。
    # 红线：分母为 0 时 completeness_rate 必须是 None——
    # "没有问题"不等于"证据完整"，空样本绝不能被算成 100%。
    # 历史任务没有 evidence_completeness 留痕，不参与分子分母，
    # 只计入 jobs_without_field 如实报告样本缺口。
    evidence_total = sum(int(record.get("evidence_total") or 0) for record in records)
    evidence_complete = sum(int(record.get("evidence_complete") or 0) for record in records)
    jobs_without_evidence_field = sum(
        1 for record in records if not record.get("has_evidence_field")
    )
    formal_issue_total = sum(int(record.get("formal_issue_total") or 0) for record in records)

    return {
        "generated_at": reference,
        "uploads_root": uploads_root.as_posix(),
        "jobs": {
            "total": total,
            "skipped_dirs": skipped,
            "scan_limit": limit,
        },
        "queue": {
            "queued_jobs": len(queued_jobs),
            "processing_jobs": len(processing_jobs),
            "backlog": len(active_jobs),
            "oldest_queued_age_seconds": round(oldest_queued_age, 2)
            if oldest_queued_age is not None
            else None,
            **heartbeats,
        },
        "stage_durations": {
            name: _summarize_durations(values) for name, values in stage_durations.items()
        },
        "ai": {
            "jobs_requested": len(ai_requested),
            "jobs_failed": len(ai_failed),
            "failure_rate": _ratio(len(ai_failed), len(ai_requested)),
            "provider_fallback_total": sum(
                int(record.get("provider_fallbacks") or 0) for record in records
            ),
        },
        "quality": {
            "unknown_report_kind": {
                "count": len(unknown_kind),
                "ratio": _ratio(len(unknown_kind), total),
            },
            "review_required": {
                "count": len(review_required),
                "ratio": _ratio(len(review_required), total),
            },
            "unresolved_report_year": {
                "count": len(unresolved_year),
                "ratio": _ratio(len(unresolved_year), total),
            },
            "error_jobs": {"count": len(error_jobs), "ratio": _ratio(len(error_jobs), total)},
            "evidence_degraded_findings": sum(
                int(record.get("evidence_degraded_count") or 0) for record in records
            ),
            # 正式问题总数（count_formal_findings 口径的唯一聚合出口，Task 7.2 补充）
            "formal_issue_total": formal_issue_total,
            "evidence_completeness": {
                "findings_total": evidence_total,
                "findings_complete": evidence_complete,
                "completeness_rate": round(evidence_complete / evidence_total, 4)
                if evidence_total
                else None,
                "jobs_without_field": jobs_without_evidence_field,
            },
        },
        "report_id": report_id_uniqueness(records),
    }


def collect_metrics_cached(
    uploads_root: Path,
    *,
    ttl_seconds: Optional[float] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """带 TTL 缓存的采集入口，防止指标端点被高频抓取时反复全量扫盘。"""
    reference = time.time() if now is None else now
    ttl = (
        ttl_seconds
        if ttl_seconds is not None
        else _env_float("METRICS_CACHE_TTL_SECONDS", _DEFAULT_CACHE_TTL_SECONDS)
    )
    key = uploads_root.as_posix()
    cached = _cache.get("payload")
    if (
        ttl > 0
        and cached is not None
        and _cache.get("key") == key
        and reference - float(_cache.get("ts") or 0.0) < ttl
    ):
        payload = dict(cached)
        payload["cached"] = True
        return payload

    payload = collect_metrics(uploads_root, now=reference)
    _cache["key"] = key
    _cache["ts"] = reference
    _cache["payload"] = payload
    result = dict(payload)
    result["cached"] = False
    return result


def reset_metrics_cache() -> None:
    """清空缓存（测试与手工排障用）。"""
    _cache["key"] = None
    _cache["ts"] = 0.0
    _cache["payload"] = None


# ---------------------------------------------------------------------------
# Prometheus 文本暴露格式
#
# 给出这一格式是为了让 docs 里的告警阈值可以直接落成告警规则，
# 而不是停留在"文档里写了个数字、没人能配上去"。
# ---------------------------------------------------------------------------
_PROM_PREFIX = "govbudget"


def _prom_lines(name: str, help_text: str, value: Any) -> List[str]:
    number = _as_float(value)
    if number is None:
        return []
    metric = f"{_PROM_PREFIX}_{name}"
    return [
        f"# HELP {metric} {help_text}",
        f"# TYPE {metric} gauge",
        f"{metric} {number}",
    ]


def render_prometheus(metrics: Dict[str, Any]) -> str:
    """把指标渲染成 Prometheus 文本暴露格式（缺失值直接省略，不写 0 冒充）。"""
    queue = metrics.get("queue") or {}
    ai = metrics.get("ai") or {}
    quality = metrics.get("quality") or {}
    report_id = metrics.get("report_id") or {}
    stages = metrics.get("stage_durations") or {}
    jobs = metrics.get("jobs") or {}

    lines: List[str] = []
    lines += _prom_lines("jobs_total", "任务产物总数", jobs.get("total"))
    lines += _prom_lines("queue_backlog", "队列积压任务数（queued+processing）", queue.get("backlog"))
    lines += _prom_lines("queue_queued_jobs", "处于 queued 的任务数", queue.get("queued_jobs"))
    lines += _prom_lines(
        "queue_oldest_queued_age_seconds",
        "最久未被消费的 queued 任务等待秒数",
        queue.get("oldest_queued_age_seconds"),
    )
    lines += _prom_lines("queue_live_workers", "心跳新鲜的 worker 数", queue.get("live_workers"))
    lines += _prom_lines(
        "queue_stale_heartbeats", "心跳过期的 worker 数", queue.get("stale_heartbeats")
    )
    lines += _prom_lines("ai_failure_rate", "请求 AI 的任务中失败/降级比例", ai.get("failure_rate"))
    lines += _prom_lines(
        "unknown_report_kind_ratio",
        "材料类型无法识别的任务比例",
        (quality.get("unknown_report_kind") or {}).get("ratio"),
    )
    lines += _prom_lines(
        "review_required_ratio",
        "转人工复核的任务比例",
        (quality.get("review_required") or {}).get("ratio"),
    )
    lines += _prom_lines(
        "unresolved_report_year_ratio",
        "年度无法识别的任务比例",
        (quality.get("unresolved_report_year") or {}).get("ratio"),
    )
    lines += _prom_lines(
        "error_job_ratio", "终态为 error 的任务比例", (quality.get("error_jobs") or {}).get("ratio")
    )
    lines += _prom_lines(
        "evidence_degraded_findings",
        "因缺证据被降级的问题条数",
        quality.get("evidence_degraded_findings"),
    )
    lines += _prom_lines(
        "formal_issue_total",
        "正式问题总数（count_formal_findings 口径）",
        quality.get("formal_issue_total"),
    )
    # 证据完整率：空样本（分母为 0）时为 None，_prom_lines 会直接省略该行，
    # 绝不输出 0 或 1 冒充"全部不完整"或"全部完整"。
    lines += _prom_lines(
        "evidence_completeness_rate",
        "正式问题证据完整率（空样本时无此指标）",
        (quality.get("evidence_completeness") or {}).get("completeness_rate"),
    )
    lines += _prom_lines(
        "report_id_collision_count", "report_id 冲突数", report_id.get("collision_count")
    )
    for stage_name, summary in stages.items():
        if not isinstance(summary, dict):
            continue
        lines += _prom_lines(
            f"stage_duration_p95_ms_{stage_name}",
            f"{stage_name} 阶段耗时 p95（毫秒）",
            summary.get("p95_ms"),
        )
        lines += _prom_lines(
            f"stage_duration_max_ms_{stage_name}",
            f"{stage_name} 阶段耗时最大值（毫秒）",
            summary.get("max_ms"),
        )
    return "\n".join(lines) + "\n"
