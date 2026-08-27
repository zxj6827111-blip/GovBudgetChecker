#!/usr/bin/env python
"""回放结构性指标门禁（Task 15.2 / 缺口 P2-06）。

它检查什么
----------
把 `scripts/replay_analysis.py` 的回放结果按阈值卡一遍，覆盖五条**结构性**红线：

| 检查项 | 含义 | 对应缺口 |
|---|---|---|
| `report_id_uniqueness` | 不同任务不得共用同一个报告身份 | P0-09 |
| `completed_jobs_have_page_coverage` | 完成态任务必须带页面覆盖率，否则无法判断"是不是没查完" | B-01 / B-03 |
| `done_jobs_min_page_coverage` | `done` 任务的覆盖率必须达阈值，低覆盖只能进 `review_required` | B-01 / B-03 |
| `evidence_completeness_rate` | 正式问题必须带完整证据 | P0-07 |
| `unknown_report_kind_ratio` | 类型识别失败比例上限 | P0-04 / P1-05 |

它**不能**检查什么（必须如实告知）
--------------------------------
没有 Golden Corpus，就没有召回率与精确率。上面五条全绿，只能说明
"没有静默失败、没有虚假成功、结构性指标达标"，**不能说明业务漏检率达标**。
把这条门禁当作"业务质量已验证"是错误解读，详见 `docs/CI_BUSINESS_GATE.md`。

数据来源
--------
- `--report <json>`：直接检查已有回放报告；
- `--uploads <dir>`：先回放该目录再检查（只读）；
- `--allow-missing`：目录/报告不存在时打印跳过原因并以 0 退出。
  CI 上没有真实 `uploads/`，用固定的 fixture 语料跑，保证门禁既不必然失败也不必然通过。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.replay_analysis import build_report  # noqa: E402
from src.schemas.issues import COMPLETED_JOB_STATUSES  # noqa: E402

DEFAULT_MIN_PAGE_COVERAGE = 0.8
DEFAULT_MIN_EVIDENCE_RATE = 0.99
DEFAULT_MAX_UNKNOWN_KIND_RATIO = 0.35


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


def _jobs(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    jobs = report.get("jobs")
    return [item for item in jobs if isinstance(item, dict)] if isinstance(jobs, list) else []


def check_report_id_uniqueness(report: Dict[str, Any]) -> CheckResult:
    summary = report.get("summary") or {}
    uniqueness = summary.get("report_id_uniqueness") or {}
    collisions = int(uniqueness.get("collision_count") or 0)
    if collisions == 0:
        return CheckResult(
            "report_id_uniqueness",
            True,
            f"无冲突（有 report_id 的任务 {uniqueness.get('jobs_with_report_id', 0)} 个）",
        )
    ids = [str(item.get("report_id")) for item in (uniqueness.get("collisions") or [])]
    return CheckResult(
        "report_id_uniqueness", False, f"{collisions} 组 report_id 被多个任务共用：{ids}"
    )


def check_completed_jobs_have_page_coverage(report: Dict[str, Any]) -> CheckResult:
    missing = [
        str(job.get("job_id"))
        for job in _jobs(report)
        if str(job.get("status") or "") in COMPLETED_JOB_STATUSES
        and not isinstance(job.get("page_coverage"), (int, float))
    ]
    if not missing:
        return CheckResult("completed_jobs_have_page_coverage", True, "完成态任务均带页面覆盖率")
    return CheckResult(
        "completed_jobs_have_page_coverage",
        False,
        f"{len(missing)} 个完成态任务没有 page_coverage（无法判断是否漏检）：{missing[:5]}",
    )


def check_done_jobs_min_page_coverage(
    report: Dict[str, Any], minimum: float
) -> CheckResult:
    offenders = []
    for job in _jobs(report):
        if str(job.get("status") or "") != "done":
            continue
        coverage = job.get("page_coverage")
        if isinstance(coverage, (int, float)) and float(coverage) < minimum:
            offenders.append((str(job.get("job_id")), round(float(coverage), 4)))
    if not offenders:
        return CheckResult(
            "done_jobs_min_page_coverage", True, f"所有 done 任务覆盖率 >= {minimum}"
        )
    return CheckResult(
        "done_jobs_min_page_coverage",
        False,
        f"{len(offenders)} 个 done 任务覆盖率低于 {minimum}，本应转 review_required：{offenders[:5]}",
    )


def check_evidence_completeness(report: Dict[str, Any], minimum: float) -> CheckResult:
    summary = report.get("summary") or {}
    evidence = summary.get("evidence_completeness") or {}
    total = int(evidence.get("findings_total") or 0)
    rate = float(evidence.get("completeness_rate") or 0.0)
    if total == 0:
        return CheckResult(
            "evidence_completeness_rate", True, "语料内没有正式问题，跳过证据完整率判定"
        )
    if rate >= minimum:
        return CheckResult(
            "evidence_completeness_rate", True, f"完整率 {rate} >= {minimum}（{total} 条）"
        )
    return CheckResult(
        "evidence_completeness_rate",
        False,
        f"完整率 {rate} 低于 {minimum}（{total} 条正式问题）",
    )


def check_unknown_report_kind_ratio(report: Dict[str, Any], maximum: float) -> CheckResult:
    summary = report.get("summary") or {}
    unknown = summary.get("unknown_report_kind") or {}
    ratio = float(unknown.get("ratio") or 0.0)
    if ratio <= maximum:
        return CheckResult("unknown_report_kind_ratio", True, f"unknown 比例 {ratio} <= {maximum}")
    return CheckResult(
        "unknown_report_kind_ratio",
        False,
        f"unknown 比例 {ratio} 超过上限 {maximum}（{unknown.get('count')} 个任务）",
    )


def evaluate(
    report: Dict[str, Any],
    *,
    min_page_coverage: float = DEFAULT_MIN_PAGE_COVERAGE,
    min_evidence_rate: float = DEFAULT_MIN_EVIDENCE_RATE,
    max_unknown_kind_ratio: float = DEFAULT_MAX_UNKNOWN_KIND_RATIO,
) -> List[CheckResult]:
    return [
        check_report_id_uniqueness(report),
        check_completed_jobs_have_page_coverage(report),
        check_done_jobs_min_page_coverage(report, min_page_coverage),
        check_evidence_completeness(report, min_evidence_rate),
        check_unknown_report_kind_ratio(report, max_unknown_kind_ratio),
    ]


def load_report(
    *,
    report_path: Optional[str],
    uploads: Optional[str],
) -> Optional[Dict[str, Any]]:
    """返回回放报告；数据源不存在时返回 None（由调用方决定跳过还是失败）。"""
    if report_path:
        path = Path(report_path).expanduser()
        if not path.is_file():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    if uploads:
        root = Path(uploads).expanduser().resolve()
        if not root.is_dir():
            return None
        return build_report(root, include_jobs=True)
    return None


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="回放结构性指标门禁（无 Golden Corpus，仅结构性）")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--report", help="已有的回放报告 JSON")
    source.add_argument("--uploads", help="任务产物目录（先只读回放再判定）")
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="数据源不存在时打印跳过原因并以 0 退出（CI 上没有真实 uploads 时用）",
    )
    parser.add_argument("--min-page-coverage", type=float, default=DEFAULT_MIN_PAGE_COVERAGE)
    parser.add_argument("--min-evidence-rate", type=float, default=DEFAULT_MIN_EVIDENCE_RATE)
    parser.add_argument(
        "--max-unknown-kind-ratio", type=float, default=DEFAULT_MAX_UNKNOWN_KIND_RATIO
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    report = load_report(report_path=args.report, uploads=args.uploads)

    if report is None:
        source = args.report or args.uploads
        message = f"数据源不存在，跳过业务门禁：{source}"
        if args.allow_missing:
            print(f"SKIP: {message}")
            print("提示：这不代表业务质量达标，只代表本次没有可判定的数据。")
            return 0
        print(f"FAIL: {message}", file=sys.stderr)
        return 2

    results = evaluate(
        report,
        min_page_coverage=args.min_page_coverage,
        min_evidence_rate=args.min_evidence_rate,
        max_unknown_kind_ratio=args.max_unknown_kind_ratio,
    )
    failed = [item for item in results if not item.passed]

    if args.json:
        print(
            json.dumps(
                {
                    "job_total": (report.get("summary") or {}).get("job_total"),
                    "passed": not failed,
                    "checks": [item.to_dict() for item in results],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        job_total = (report.get("summary") or {}).get("job_total")
        print(f"回放语料任务数：{job_total}")
        for item in results:
            print(f"[{'PASS' if item.passed else 'FAIL'}] {item.name}: {item.detail}")
        print(
            "\n局限：本门禁只覆盖结构性指标。无 Golden Corpus，"
            "因此**不度量召回率与精确率**，全绿不等于业务质量达标。"
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
