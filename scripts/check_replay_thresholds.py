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
| `evidence_completeness_rate` | 可定位类正式问题必须带完整证据（BUD-001 类文档级规则天然无页码，单列不进分母） | P0-07 |
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
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.replay_analysis import build_report, summarize  # noqa: E402
from src.schemas.issues import COMPLETED_JOB_STATUSES  # noqa: E402

# Windows 默认控制台编码可能无法显示中文任务名/失败原因；门禁输出本身
# 也是验收证据，统一使用 UTF-8，不能因为打印阶段乱码而丢失诊断信息。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # 非 TextIO（如 pytest 捕获流）：跳过
            pass

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


def _valid_ratio(value: Any) -> Optional[float]:
    """只接受有限、闭区间 [0, 1] 的真实比例；缺失按不可判定处理。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    ratio = float(value)
    if not math.isfinite(ratio) or not 0.0 <= ratio <= 1.0:
        return None
    return ratio


def _threshold_arg(value: str) -> float:
    """解析 CLI 比率阈值，拒绝 NaN/Inf 和越界值，避免阈值制造假绿。"""
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("阈值必须是数字") from exc
    if _valid_ratio(parsed) is None:
        raise argparse.ArgumentTypeError("阈值必须是有限值且位于 [0, 1]")
    return parsed


def _valid_count(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _rounded_ratio(count: int, total: int) -> Optional[float]:
    """按回放报告的四位小数口径重算比例。"""
    return round(count / total, 4) if total else None


def _summary_mismatch(
    summary: Dict[str, Any], jobs: List[Dict[str, Any]]
) -> Optional[str]:
    """Return the first aggregate mismatch between ``summary`` and ``jobs``.

    The report contains both a per-task ledger and a derived summary.  Checking
    only ``summary`` allows a damaged or hand-edited summary to make the gate
    green while the task rows say something else.  Recompute the same summary
    used by ``replay_analysis`` and compare every derived field, including
    nested counts and rates.  A malformed job is reported as an integrity
    failure rather than leaking a ``KeyError`` out of the gate.
    """
    try:
        expected = summarize(jobs)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        return f"无法从 jobs 重算汇总：{type(exc).__name__}: {exc}（fail-closed）"

    for key, expected_value in expected.items():
        if key not in summary:
            return f"summary 缺少由 jobs 派生的字段 {key!r}（fail-closed）"
        if summary[key] != expected_value:
            return (
                f"summary.{key} 与 jobs 重算结果不一致："
                f"reported={summary[key]!r} expected={expected_value!r}（fail-closed）"
            )
    return None


def check_replay_integrity(report: Dict[str, Any]) -> CheckResult:
    """回放产物完整性门禁，防止跳过/删行后仍用剩余样本假绿。"""
    raw_jobs = report.get("jobs")
    if not isinstance(raw_jobs, list) or not raw_jobs:
        return CheckResult(
            "replay_integrity", False, "缺少逐任务 jobs（fail-closed）"
        )
    if any(not isinstance(item, dict) for item in raw_jobs):
        return CheckResult("replay_integrity", False, "jobs 含非法任务记录（fail-closed）")

    skipped_count = _valid_count(report.get("skipped_count"))
    skipped_dirs = report.get("skipped_dirs")
    if skipped_count is None or not isinstance(skipped_dirs, list):
        return CheckResult(
            "replay_integrity", False, "缺少 skipped_count/skipped_dirs（fail-closed）"
        )
    if skipped_count != len(skipped_dirs):
        return CheckResult(
            "replay_integrity",
            False,
            f"skipped_count={skipped_count} 与 skipped_dirs={len(skipped_dirs)} 不一致",
        )
    if skipped_count:
        return CheckResult(
            "replay_integrity",
            False,
            f"有 {skipped_count} 个任务目录未能读取，不能用剩余样本判定 GO",
        )

    summary = report.get("summary")
    if not isinstance(summary, dict):
        return CheckResult("replay_integrity", False, "缺少 summary（fail-closed）")
    job_total = _valid_count(summary.get("job_total"))
    if job_total is None or job_total != len(raw_jobs):
        return CheckResult(
            "replay_integrity",
            False,
            f"summary.job_total={summary.get('job_total')!r} 与 jobs={len(raw_jobs)} 不一致",
        )
    malformed = [
        str(index)
        for index, job in enumerate(raw_jobs)
        if not str(job.get("job_id") or "").strip()
        or not str(job.get("status") or "").strip()
    ]
    if malformed:
        return CheckResult(
            "replay_integrity",
            False,
            f"jobs 缺少 job_id/status：记录 {malformed[:5]}（fail-closed）",
        )
    mismatch = _summary_mismatch(summary, raw_jobs)
    if mismatch:
        return CheckResult("replay_integrity", False, mismatch)
    return CheckResult(
        "replay_integrity", True, "回放目录、逐任务明细和汇总（含派生指标）一致"
    )


def check_report_id_uniqueness(report: Dict[str, Any]) -> CheckResult:
    summary = report.get("summary") or {}
    if not isinstance(summary, dict):
        return CheckResult("report_id_uniqueness", False, "缺少可验证的 summary（fail-closed）")
    uniqueness = summary.get("report_id_uniqueness")
    if not isinstance(uniqueness, dict):
        return CheckResult(
            "report_id_uniqueness", False, "缺少 report_id_uniqueness（fail-closed）"
        )
    collisions = _valid_count(uniqueness.get("collision_count"))
    collision_details = uniqueness.get("collisions")
    job_total = _valid_count(summary.get("job_total"))
    total_jobs = _valid_count(uniqueness.get("total_jobs"))
    jobs_with_report_id = _valid_count(uniqueness.get("jobs_with_report_id"))
    jobs_without_report_id = _valid_count(uniqueness.get("jobs_without_report_id"))
    if (
        collisions is None
        or not isinstance(collision_details, list)
        or any(not isinstance(item, dict) for item in collision_details)
    ):
        return CheckResult(
            "report_id_uniqueness",
            False,
            "report_id_uniqueness 字段缺失或类型非法（fail-closed）",
        )
    if (
        job_total is None
        or total_jobs is None
        or jobs_with_report_id is None
        or jobs_without_report_id is None
        or total_jobs != job_total
        or jobs_with_report_id + jobs_without_report_id != job_total
    ):
        return CheckResult(
            "report_id_uniqueness",
            False,
            "report_id 覆盖计数缺失或与 job_total 不一致（fail-closed）",
        )
    if collisions != len(collision_details):
        return CheckResult(
            "report_id_uniqueness",
            False,
            f"collision_count={collisions} 与 collisions 明细数不一致（fail-closed）",
        )
    if collisions == 0:
        if jobs_without_report_id:
            return CheckResult(
                "report_id_uniqueness",
                False,
                f"有 {jobs_without_report_id} 个任务缺少 report_id，无法证明身份唯一",
            )
        return CheckResult(
            "report_id_uniqueness",
            True,
            f"无冲突（{jobs_with_report_id} 个任务均有 report_id）",
        )
    ids = [str(item.get("report_id")) for item in collision_details]
    return CheckResult(
        "report_id_uniqueness", False, f"{collisions} 组 report_id 被多个任务共用：{ids}"
    )


def check_completed_jobs_have_page_coverage(report: Dict[str, Any]) -> CheckResult:
    raw_jobs = report.get("jobs")
    if not isinstance(raw_jobs, list) or not raw_jobs:
        return CheckResult(
            "completed_jobs_have_page_coverage",
            False,
            "缺少可评估的 jobs（空报告或未包含逐任务明细，fail-closed）",
        )
    if any(not isinstance(item, dict) for item in raw_jobs):
        return CheckResult(
            "completed_jobs_have_page_coverage",
            False,
            "jobs 含非法任务记录（fail-closed）",
        )
    malformed = [
        str(index)
        for index, job in enumerate(raw_jobs)
        if not str(job.get("job_id") or "").strip() or not str(job.get("status") or "").strip()
    ]
    missing = [
        str(job.get("job_id"))
        for job in _jobs(report)
        if str(job.get("status") or "") in COMPLETED_JOB_STATUSES
        and _valid_ratio(job.get("page_coverage")) is None
    ]
    if not malformed and not missing:
        return CheckResult("completed_jobs_have_page_coverage", True, "完成态任务均带页面覆盖率")
    details = []
    if malformed:
        details.append(f"jobs 缺少 job_id/status：记录 {malformed[:5]}（fail-closed）")
    if missing:
        details.append(f"{len(missing)} 个完成态任务没有 page_coverage：{missing[:5]}")
    return CheckResult(
        "completed_jobs_have_page_coverage",
        False,
        "; ".join(details),
    )


def check_done_jobs_min_page_coverage(report: Dict[str, Any], minimum: float) -> CheckResult:
    offenders = []
    for job in _jobs(report):
        if str(job.get("status") or "") != "done":
            continue
        coverage = _valid_ratio(job.get("page_coverage"))
        # 缺失/非法覆盖率由 completed_jobs_have_page_coverage 统一报出，
        # 避免同一坏任务同时污染两条独立门禁；这里仅判定可解析值的阈值。
        if coverage is not None and coverage < minimum:
            offenders.append((str(job.get("job_id")), round(coverage, 4)))
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
    """证据完整率门禁（B1 口径：只看可定位类 finding）。

    BUD-001（缺预算表/缺必备说明章节）类文档级规则天然无页码，单独计数
    （``document_level_findings_total``），不进入分母；门禁判定用的是
    ``locatable_completeness_rate``。旧报告没有可定位类字段时回退全量口径
    （``completeness_rate``），保证历史报告仍可判定——回退时在 detail 里
    明确标注，避免两种口径被误读成同一个数。
    """
    summary = report.get("summary") or {}
    if not isinstance(summary, dict):
        return CheckResult(
            "evidence_completeness_rate", False, "缺少可验证的 summary（fail-closed）"
        )
    evidence = summary.get("evidence_completeness")
    if not isinstance(evidence, dict):
        return CheckResult(
            "evidence_completeness_rate",
            False,
            "缺少 evidence_completeness（fail-closed）",
        )
    findings_total = _valid_count(evidence.get("findings_total"))
    findings_complete = _valid_count(evidence.get("findings_complete"))
    full_rate = _valid_ratio(evidence.get("completeness_rate"))
    if findings_total is None or findings_complete is None:
        return CheckResult(
            "evidence_completeness_rate",
            False,
            "evidence_completeness 全量计数缺失或非法（fail-closed）",
        )
    if findings_complete > findings_total:
        return CheckResult(
            "evidence_completeness_rate",
            False,
            "findings_complete 不能大于 findings_total（fail-closed）",
        )
    expected_full_rate = _rounded_ratio(findings_complete, findings_total)
    if full_rate != expected_full_rate:
        return CheckResult(
            "evidence_completeness_rate",
            False,
            f"全量证据比例不一致：reported={evidence.get('completeness_rate')!r} "
            f"expected={expected_full_rate!r}（fail-closed）",
        )

    use_locatable = evidence.get("locatable_findings_total") is not None
    if use_locatable:
        total = _valid_count(evidence.get("locatable_findings_total"))
        complete = _valid_count(evidence.get("locatable_findings_complete"))
        raw_rate = evidence.get("locatable_completeness_rate")
        doc_total = _valid_count(evidence.get("document_level_findings_total"))
        basis = f"可定位类 {total} 条（文档级单列 {doc_total} 条）"
    else:
        total = findings_total
        complete = findings_complete
        raw_rate = evidence.get("completeness_rate")
        doc_total = None
        basis = f"全量口径 {total} 条（旧报告无可定位类字段，未剔除文档级）"
    rate_key = "locatable_completeness_rate" if use_locatable else "completeness_rate"
    if rate_key not in evidence:
        return CheckResult(
            "evidence_completeness_rate",
            False,
            f"evidence_completeness.{rate_key} 字段缺失（fail-closed）",
        )
    if total is None or complete is None or (doc_total is None and use_locatable):
        return CheckResult(
            "evidence_completeness_rate",
            False,
            "evidence_completeness 计数字段缺失或非法（fail-closed）",
        )
    if complete > total:
        return CheckResult(
            "evidence_completeness_rate",
            False,
            "可定位 findings_complete 不能大于 findings_total（fail-closed）",
        )
    rate = _valid_ratio(raw_rate)
    expected_rate = _rounded_ratio(complete, total)
    if rate != expected_rate:
        return CheckResult(
            "evidence_completeness_rate",
            False,
            f"证据比例不一致：reported={raw_rate!r} expected={expected_rate!r} "
            "（fail-closed）",
        )
    if total == 0:
        skip_note = "语料内没有可定位类正式问题，跳过证据完整率判定"
        if doc_total:
            skip_note += f"（文档级单列 {doc_total} 条，不构成证据缺口）"
        return CheckResult("evidence_completeness_rate", True, skip_note)
    if rate is not None and rate >= minimum:
        return CheckResult(
            "evidence_completeness_rate", True, f"完整率 {rate} >= {minimum}（{basis}）"
        )
    shown_rate = rate if rate is not None else "缺失"
    return CheckResult(
        "evidence_completeness_rate",
        False,
        f"完整率 {shown_rate} 低于 {minimum}（{basis}）",
    )


def check_unknown_report_kind_ratio(report: Dict[str, Any], maximum: float) -> CheckResult:
    summary = report.get("summary") or {}
    if not isinstance(summary, dict):
        return CheckResult(
            "unknown_report_kind_ratio", False, "缺少可验证的 summary（fail-closed）"
        )
    unknown = summary.get("unknown_report_kind")
    if not isinstance(unknown, dict):
        return CheckResult(
            "unknown_report_kind_ratio",
            False,
            "缺少 unknown_report_kind（fail-closed）",
        )
    count = _valid_count(unknown.get("count"))
    ratio = _valid_ratio(unknown.get("ratio"))
    job_total = _valid_count(summary.get("job_total"))
    if count is None or ratio is None or job_total is None:
        return CheckResult(
            "unknown_report_kind_ratio",
            False,
            "unknown_report_kind.count/ratio 缺失或非法（fail-closed）",
        )
    if count > job_total:
        return CheckResult(
            "unknown_report_kind_ratio",
            False,
            f"unknown count={count} 大于 job_total={job_total}（fail-closed）",
        )
    expected_ratio = round(count / job_total, 4) if job_total else 0.0
    if ratio != expected_ratio:
        return CheckResult(
            "unknown_report_kind_ratio",
            False,
            f"unknown 比例不一致：reported={unknown.get('ratio')!r} "
            f"expected={expected_ratio!r}（fail-closed）",
        )
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
        check_replay_integrity(report),
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
    parser.add_argument(
        "--min-page-coverage", type=_threshold_arg, default=DEFAULT_MIN_PAGE_COVERAGE
    )
    parser.add_argument(
        "--min-evidence-rate", type=_threshold_arg, default=DEFAULT_MIN_EVIDENCE_RATE
    )
    parser.add_argument(
        "--max-unknown-kind-ratio",
        type=_threshold_arg,
        default=DEFAULT_MAX_UNKNOWN_KIND_RATIO,
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
    summary = report.get("summary")
    job_total = summary.get("job_total") if isinstance(summary, dict) else None

    if args.json:
        print(
            json.dumps(
                {
                    "job_total": job_total,
                    "passed": not failed,
                    "checks": [item.to_dict() for item in results],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
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
