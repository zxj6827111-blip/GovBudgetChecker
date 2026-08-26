#!/usr/bin/env python
"""离线回放既有任务产物，汇总结构性质量指标（缺口 B-04 的框架部分）。

用途
----
把 `UPLOAD_DIR` 下已有的任务产物（`status.json`）批量读一遍，重算结构性指标并输出
JSON 报告，用于回答"整改前后，覆盖率/unknown 比例/终态分布/证据完整率有没有变好"。

本轮范围与局限（务必如实理解）
------------------------------
- 这是**离线指标回放**：只读既有产物重新计算指标，**不重跑解析、不调用 AI、不写任何
  既有目录**。因此它衡量的是"产物里记录了什么"，不是"重跑一遍会得到什么"。
- 本轮**不建 Golden Corpus、不做人工标注**，因此**只有结构性指标，没有召回率/精确率**。
  在 Golden Corpus 建立前，这些指标只能证明"没有静默失败、没有虚假成功"，
  不能证明业务召回率达标。
- 输出必须由 `--output` 显式指定路径，且不允许写进被扫描的产物目录，
  避免度量动作本身污染被度量的数据。

用法
----
    python scripts/replay_analysis.py --uploads uploads --output outputs/replay.json
    python scripts/replay_analysis.py --output outputs/replay.json --limit 50 --print-summary
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.schemas.issues import (  # noqa: E402  (需先补 sys.path 才能导入仓库模块)
    COMPLETED_JOB_STATUSES,
    infer_analysis_conclusion,
    normalize_job_status,
)
from src.services.evidence_guard import (  # noqa: E402
    apply_evidence_completeness,
    count_formal_findings,
)
from src.utils.provenance import ENGINE_VERSION  # noqa: E402
from src.utils.report_year import parse_report_year  # noqa: E402

#: 页面覆盖率分桶边界（左闭右开，最后一档为 [1.0, 1.0]）
_COVERAGE_BUCKETS: Tuple[Tuple[str, float, float], ...] = (
    ("lt_0.5", 0.0, 0.5),
    ("0.5_to_0.8", 0.5, 0.8),
    ("0.8_to_1.0", 0.8, 1.0),
    ("eq_1.0", 1.0, 1.0),
)


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


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
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _result_meta(payload: Dict[str, Any]) -> Dict[str, Any]:
    result = payload.get("result")
    if not isinstance(result, dict):
        return {}
    meta = result.get("meta")
    return meta if isinstance(meta, dict) else {}


def _resolve_page_coverage(payload: Dict[str, Any]) -> Optional[float]:
    meta = _result_meta(payload)
    page_extraction = meta.get("page_extraction")
    candidates: List[Any] = [payload.get("page_coverage")]
    if isinstance(page_extraction, dict):
        candidates.append(page_extraction.get("page_coverage"))
    for candidate in candidates:
        value = _as_float(candidate)
        if value is not None:
            return value
    return None


def _resolve_scanned_page_count(payload: Dict[str, Any]) -> Optional[int]:
    meta = _result_meta(payload)
    page_extraction = meta.get("page_extraction")
    candidates: List[Any] = [payload.get("scanned_page_count")]
    if isinstance(page_extraction, dict):
        candidates.append(page_extraction.get("scanned_page_count"))
    for candidate in candidates:
        value = _as_int(candidate)
        if value is not None:
            return value
    return None


def _resolve_report_kind(payload: Dict[str, Any]) -> str:
    meta = _result_meta(payload)
    for candidate in (payload.get("report_kind"), meta.get("report_kind")):
        kind = str(candidate or "").strip().lower()
        if kind:
            return kind
    return "unknown"


def _resolve_report_year(payload: Dict[str, Any]) -> Optional[int]:
    """复用 M1 的唯一权威年份解析实现，识别不到就是 None（不兜底）。"""
    meta = _result_meta(payload)
    for candidate in (
        payload.get("report_year"),
        meta.get("report_year"),
        payload.get("fiscal_year"),
        meta.get("fiscal_year"),
    ):
        year = parse_report_year(candidate)
        if year is not None:
            return year
    return None


def _resolve_evidence_metrics(payload: Dict[str, Any]) -> Dict[str, Any]:
    """取证据完整率。

    新任务的 `result.meta.evidence_completeness` 直接可用；历史任务没有该字段时，
    对结果的**深拷贝**重新跑一遍校验逻辑得出指标——深拷贝保证既有产物不被改写，
    复用同一函数保证与线上口径一致。
    """
    meta = _result_meta(payload)
    recorded = meta.get("evidence_completeness")
    if isinstance(recorded, dict) and _as_int(recorded.get("total")) is not None:
        return {
            "source": "recorded",
            "total": _as_int(recorded.get("total")) or 0,
            "complete": _as_int(recorded.get("complete")) or 0,
            "degraded_count": _as_int(recorded.get("degraded_count")) or 0,
            "rule_warning_count": _as_int(recorded.get("rule_warning_count")) or 0,
        }

    result = payload.get("result")
    if not isinstance(result, dict):
        return {
            "source": "unavailable",
            "total": 0,
            "complete": 0,
            "degraded_count": 0,
            "rule_warning_count": 0,
        }

    recomputed = apply_evidence_completeness(copy.deepcopy(result))
    return {
        "source": "recomputed",
        "total": int(recomputed["total"]),
        "complete": int(recomputed["complete"]),
        "degraded_count": int(recomputed["degraded_count"]),
        "rule_warning_count": int(recomputed["rule_warning_count"]),
    }


def _resolve_report_id(payload: Dict[str, Any]) -> Optional[str]:
    structured = payload.get("structured_ingest")
    if not isinstance(structured, dict):
        meta = _result_meta(payload)
        structured = meta.get("structured_ingest") if isinstance(meta, dict) else None
    if not isinstance(structured, dict):
        return None
    ps_sync = structured.get("ps_sync")
    if not isinstance(ps_sync, dict):
        return None
    report_id = str(ps_sync.get("report_id") or "").strip()
    return report_id or None


def _status_label(raw_status: str, normalized: Optional[str]) -> str:
    """状态分布用的标签。

    无法归一的状态（例如"上传完成但从未分析"的 `uploaded`）如实保留原值，
    而不是笼统塞进 unrecognized——真实产物里这类任务占比不低，
    混成一档会让报告失去诊断价值。这里只影响回放报告，不改动 M1 的状态模型。
    """
    if normalized:
        return normalized
    text = str(raw_status or "").strip().lower()
    return f"unnormalized:{text}" if text else "missing_status"


def collect_job_record(job_dir: Path) -> Optional[Dict[str, Any]]:
    """读取单个任务产物，产出一条只读指标记录；无 status.json 时返回 None。"""
    status_file = job_dir / "status.json"
    if not status_file.is_file():
        return None
    payload = _read_json(status_file)
    if not payload:
        return None

    result = payload.get("result") if isinstance(payload.get("result"), dict) else None
    raw_status = str(payload.get("status") or "")
    normalized_status = normalize_job_status(raw_status)
    formal_issue_total = count_formal_findings(result) if result else 0

    return {
        "job_id": str(payload.get("job_id") or job_dir.name),
        "filename": _basename(payload.get("filename") or payload.get("saved_path")),
        "checksum": str(payload.get("checksum") or "").strip() or None,
        "raw_status": raw_status,
        "status": normalized_status,
        "status_label": _status_label(raw_status, normalized_status),
        "analysis_conclusion": infer_analysis_conclusion(
            payload.get("status"),
            issue_total=formal_issue_total,
            explicit_conclusion=payload.get("analysis_conclusion"),
        ),
        "quality_status": str(payload.get("quality_status") or "") or None,
        "page_coverage": _resolve_page_coverage(payload),
        "scanned_page_count": _resolve_scanned_page_count(payload),
        "report_kind": _resolve_report_kind(payload),
        "report_year": _resolve_report_year(payload),
        "formal_issue_total": formal_issue_total,
        "has_result": result is not None,
        "evidence": _resolve_evidence_metrics(payload),
        "report_id": _resolve_report_id(payload),
    }


def iter_job_dirs(uploads_root: Path) -> Iterable[Path]:
    """稳定顺序遍历任务目录，保证两台机器上的输出可比对。"""
    if not uploads_root.is_dir():
        return []
    return sorted(
        (child for child in uploads_root.iterdir() if child.is_dir() and not child.name.startswith(".")),
        key=lambda item: item.name,
    )


def _ratio(count: int, total: int) -> float:
    return round(count / total, 4) if total else 0.0


def _distribution(values: Iterable[Optional[str]], total: int) -> Dict[str, Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for value in values:
        key = value or "unrecognized"
        counts[key] = counts.get(key, 0) + 1
    return {
        key: {"count": counts[key], "ratio": _ratio(counts[key], total)}
        for key in sorted(counts)
    }


def _coverage_buckets(coverages: List[float]) -> Dict[str, Dict[str, Any]]:
    total = len(coverages)
    buckets: Dict[str, Dict[str, Any]] = {}
    for name, low, high in _COVERAGE_BUCKETS:
        if low == high:
            count = sum(1 for value in coverages if value >= high)
        else:
            count = sum(1 for value in coverages if low <= value < high)
        buckets[name] = {"count": count, "ratio": _ratio(count, total)}
    return buckets


def _report_id_uniqueness(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """report_id 唯一性检查（P0-09 的回归观测口）。

    判定标准：同一个 report_id 下出现了多个不同 checksum，说明不同原件被并到了
    同一份报告身份上。checksum 缺失的任务无法参与判定，单独计数如实说明。
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


def summarize(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """把逐任务记录汇总成结构性指标。"""
    total = len(records)
    coverages = [
        record["page_coverage"]
        for record in records
        if isinstance(record.get("page_coverage"), (int, float))
    ]
    scanned_jobs = sum(1 for record in records if (record.get("scanned_page_count") or 0) > 0)
    unknown_kind = sum(1 for record in records if record.get("report_kind") in {"", "unknown"})
    unresolved_year = sum(1 for record in records if record.get("report_year") is None)

    completed_records = [
        record for record in records if (record.get("status") or "") in COMPLETED_JOB_STATUSES
    ]
    empty_findings = sum(
        1 for record in completed_records if int(record.get("formal_issue_total") or 0) == 0
    )

    evidence_total = sum(int(record["evidence"]["total"]) for record in records)
    evidence_complete = sum(int(record["evidence"]["complete"]) for record in records)
    evidence_degraded = sum(int(record["evidence"]["degraded_count"]) for record in records)
    evidence_rule_warnings = sum(
        int(record["evidence"]["rule_warning_count"]) for record in records
    )

    return {
        "job_total": total,
        "status_distribution": _distribution(
            (record.get("status_label") for record in records), total
        ),
        "conclusion_distribution": _distribution(
            (record.get("analysis_conclusion") for record in records), total
        ),
        "page_coverage": {
            "measured_jobs": len(coverages),
            "unmeasured_jobs": total - len(coverages),
            "mean": round(sum(coverages) / len(coverages), 4) if coverages else None,
            "min": round(min(coverages), 4) if coverages else None,
            "max": round(max(coverages), 4) if coverages else None,
            "buckets": _coverage_buckets(coverages),
        },
        "scanned_page_jobs": {"count": scanned_jobs, "ratio": _ratio(scanned_jobs, total)},
        "unknown_report_kind": {"count": unknown_kind, "ratio": _ratio(unknown_kind, total)},
        "unresolved_report_year": {
            "count": unresolved_year,
            "ratio": _ratio(unresolved_year, total),
        },
        "empty_findings_jobs": {
            "count": empty_findings,
            "ratio": _ratio(empty_findings, len(completed_records)),
            "basis": "completed_jobs",
            "completed_jobs": len(completed_records),
        },
        "evidence_completeness": {
            "findings_total": evidence_total,
            "findings_complete": evidence_complete,
            "completeness_rate": _ratio(evidence_complete, evidence_total)
            if evidence_total
            else 1.0,
            "degraded_total": evidence_degraded,
            "rule_warning_total": evidence_rule_warnings,
        },
        "report_id_uniqueness": _report_id_uniqueness(records),
    }


def build_report(
    uploads_root: Path,
    *,
    limit: Optional[int] = None,
    include_jobs: bool = True,
) -> Dict[str, Any]:
    records: List[Dict[str, Any]] = []
    skipped: List[str] = []
    for job_dir in iter_job_dirs(uploads_root):
        if limit is not None and len(records) >= limit:
            break
        record = collect_job_record(job_dir)
        if record is None:
            skipped.append(job_dir.name)
            continue
        records.append(record)

    report: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "engine_version": ENGINE_VERSION,
        "uploads_root": uploads_root.as_posix(),
        "mode": "offline_metric_replay",
        "limitations": [
            "只读既有产物重算指标，不重跑解析、不调用 AI",
            "本轮无 Golden Corpus，只有结构性指标，不含召回率/精确率",
        ],
        "skipped_dirs": sorted(skipped),
        "skipped_count": len(skipped),
        "summary": summarize(records),
    }
    if include_jobs:
        report["jobs"] = records
    return report


def _resolve_output_path(raw_output: str, uploads_root: Path) -> Path:
    """解析输出路径，并拒绝写入被扫描的产物目录。"""
    output = Path(raw_output).expanduser().resolve()
    root = uploads_root.resolve()
    if output == root or root in output.parents:
        raise ValueError(
            f"拒绝把报告写入被扫描的产物目录（{root.as_posix()}）：请换一个 --output 路径"
        )
    return output


def write_report(report: Dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    # 显式 LF + UTF-8，保证 Windows 与 Linux 上生成的报告字节一致、可直接 diff
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=False)
        handle.write("\n")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="离线回放既有任务产物并汇总结构性质量指标（只读）",
    )
    parser.add_argument(
        "--uploads",
        default=os.getenv("UPLOAD_DIR", "uploads"),
        help="任务产物根目录，默认取环境变量 UPLOAD_DIR 或 ./uploads",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="报告输出路径（必须显式指定，且不能位于 --uploads 目录内）",
    )
    parser.add_argument("--limit", type=int, default=None, help="只回放前 N 个任务")
    parser.add_argument(
        "--no-jobs",
        action="store_true",
        help="只输出汇总，不输出逐任务明细",
    )
    parser.add_argument(
        "--print-summary",
        action="store_true",
        help="同时把汇总打印到标准输出",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    uploads_root = Path(args.uploads).expanduser().resolve()
    if not uploads_root.is_dir():
        print(f"产物目录不存在：{uploads_root.as_posix()}", file=sys.stderr)
        return 2

    try:
        output = _resolve_output_path(args.output, uploads_root)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    report = build_report(
        uploads_root,
        limit=args.limit,
        include_jobs=not args.no_jobs,
    )
    write_report(report, output)

    summary = report["summary"]
    print(
        "回放完成：任务 {total} 个，跳过 {skipped} 个，报告已写入 {path}".format(
            total=summary["job_total"],
            skipped=report["skipped_count"],
            path=output.as_posix(),
        )
    )
    if args.print_summary:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
