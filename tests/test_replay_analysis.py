"""Task 9 / 缺口 B-04（框架部分）：质量度量回放脚本。

断言意图：
1. 对临时目录构造的假任务产物算出正确统计（覆盖率分布、扫描页占比、unknown 比例、
   年份未识别比例、终态分布、空 findings 数、证据完整率、report_id 唯一性）；
2. 只读：跑完之后既有产物的字节内容与修改时间都不变；
3. 拒绝把报告写进被扫描的产物目录，避免度量动作污染被度量的数据；
4. 缺证据被降级的条目不计入正式问题，与线上门禁口径一致；
5. 历史产物（没有 evidence_completeness 字段）能被重算出证据指标。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from scripts.replay_analysis import (
    build_report,
    collect_job_record,
    main,
    summarize,
)


def _write_job(uploads: Path, job_id: str, payload: Dict[str, Any]) -> Path:
    job_dir = uploads / job_id
    job_dir.mkdir(parents=True)
    payload.setdefault("job_id", job_id)
    (job_dir / "status.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return job_dir


def _legacy_result(
    issues: List[Dict[str, Any]],
    *,
    page_coverage: float = 1.0,
    scanned_page_count: int = 0,
    report_kind: str = "budget",
    report_year: Any = 2025,
    evidence_completeness: Dict[str, Any] | None = None,
    include_page_extraction: bool = True,
) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "report_kind": report_kind,
        "report_year": report_year,
    }
    if include_page_extraction:
        meta["page_extraction"] = {
            "page_coverage": page_coverage,
            "scanned_page_count": scanned_page_count,
        }
    if evidence_completeness is not None:
        meta["evidence_completeness"] = evidence_completeness
    return {
        "issues": {"all": issues, "error": list(issues), "warn": [], "info": []},
        "meta": meta,
    }


@pytest.fixture
def sample_uploads(tmp_path: Path) -> Path:
    """构造 5 个任务产物，覆盖各种终态与质量情况。"""
    uploads = tmp_path / "uploads"
    uploads.mkdir()

    complete_issue = {
        "id": "C-001-1",
        "source": "rule",
        "severity": "error",
        "location": {"page": 12},
        "evidence": [{"page": 12, "text": "合计 35.20"}],
        "evidence_status": "complete",
    }

    # 1) done + 有问题 + 覆盖率 1.0
    _write_job(
        uploads,
        "job-done",
        {
            "status": "done",
            "analysis_conclusion": "findings_detected",
            "quality_status": "complete",
            "report_kind": "budget",
            "report_year": 2025,
            "page_coverage": 1.0,
            "scanned_page_count": 0,
            "checksum": "sum-done",
            "structured_ingest": {"ps_sync": {"report_id": "report-1"}},
            "result": _legacy_result(
                [complete_issue],
                evidence_completeness={
                    "total": 1,
                    "complete": 1,
                    "degraded_count": 0,
                    "rule_warning_count": 0,
                },
            ),
        },
    )

    # 2) review_required：扫描件、覆盖率 0.4
    _write_job(
        uploads,
        "job-review",
        {
            "status": "review_required",
            "analysis_conclusion": "incomplete",
            "quality_status": "review_required",
            "report_kind": "unknown",
            "report_year": None,
            "page_coverage": 0.4,
            "scanned_page_count": 3,
            "checksum": "sum-review",
            "structured_ingest": {"ps_sync": {"report_id": "report-2"}},
            "result": _legacy_result(
                [],
                page_coverage=0.4,
                scanned_page_count=3,
                report_kind="unknown",
                report_year=None,
                evidence_completeness={
                    "total": 0,
                    "complete": 0,
                    "degraded_count": 0,
                    "rule_warning_count": 0,
                },
            ),
        },
    )

    # 3) done + 0 正式问题（唯一的 AI 问题因缺证据被降级）
    _write_job(
        uploads,
        "job-degraded-evidence",
        {
            "status": "review_required",
            "analysis_conclusion": "incomplete",
            "report_kind": "final",
            "report_year": 2024,
            "page_coverage": 0.9,
            "scanned_page_count": 0,
            "checksum": "sum-degraded",
            "structured_ingest": {"ps_sync": {"report_id": "report-3"}},
            "result": {
                "ai_findings": [
                    {
                        "id": "ai-1",
                        "source": "ai",
                        "severity": "manual_review",
                        "evidence": [],
                        "evidence_status": "degraded_missing_evidence",
                    }
                ],
                "rule_findings": [],
                "meta": {
                    "report_kind": "final",
                    "report_year": 2024,
                    "page_extraction": {"page_coverage": 0.9, "scanned_page_count": 0},
                    "evidence_completeness": {
                        "total": 1,
                        "complete": 0,
                        "degraded_count": 1,
                        "rule_warning_count": 0,
                    },
                },
            },
        },
    )

    # 4) error 态
    _write_job(
        uploads,
        "job-error",
        {
            "status": "error",
            "error": "local_rules_failed:boom",
            "report_kind": "budget",
            "report_year": 2025,
            "page_coverage": 0.0,
            "scanned_page_count": 0,
            "checksum": "sum-error",
        },
    )

    # 5) 历史产物：没有 evidence_completeness，report_id 与 job-done 撞车
    _write_job(
        uploads,
        "job-legacy",
        {
            "status": "done",
            "report_kind": "budget",
            "report_year": 2023,
            "checksum": "sum-legacy",
            "structured_ingest": {"ps_sync": {"report_id": "report-1"}},
            "result": _legacy_result(
                [
                    {
                        "id": "C-002-1",
                        "source": "rule",
                        "severity": "error",
                        "location": {},
                        "evidence": [],
                    }
                ],
                report_year=2023,
                include_page_extraction=False,
            ),
        },
    )

    # 无 status.json 的目录必须被跳过而不是报错
    (uploads / "job-broken").mkdir()
    return uploads


def test_unnormalized_status_is_reported_as_is(tmp_path: Path) -> None:
    """真实产物里存在 status='uploaded'（上传完成但从未分析），必须如实分档。"""
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    _write_job(uploads, "job-uploaded", {"status": "uploaded", "checksum": "s1"})

    summary = build_report(uploads)["summary"]

    assert summary["status_distribution"] == {
        "unnormalized:uploaded": {"count": 1, "ratio": 1.0}
    }
    # 未分析的任务不进入"分析已跑完"的分母，不能被算成"空 findings"
    assert summary["empty_findings_jobs"]["completed_jobs"] == 0
    assert summary["empty_findings_jobs"]["count"] == 0


def test_missing_status_value_is_labelled(tmp_path: Path) -> None:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    _write_job(uploads, "job-no-status", {"checksum": "s1"})

    summary = build_report(uploads)["summary"]
    assert summary["status_distribution"]["missing_status"]["count"] == 1


def test_collect_job_record_reads_core_metrics(sample_uploads: Path) -> None:
    record = collect_job_record(sample_uploads / "job-review")

    assert record is not None
    assert record["status"] == "review_required"
    assert record["analysis_conclusion"] == "incomplete"
    assert record["page_coverage"] == 0.4
    assert record["scanned_page_count"] == 3
    assert record["report_kind"] == "unknown"
    assert record["report_year"] is None
    assert record["formal_issue_total"] == 0


def test_collect_job_record_skips_dir_without_status(sample_uploads: Path) -> None:
    assert collect_job_record(sample_uploads / "job-broken") is None


def test_degraded_finding_is_not_counted_as_formal(sample_uploads: Path) -> None:
    """与线上门禁同口径：缺证据降级项不算正式问题。"""
    record = collect_job_record(sample_uploads / "job-degraded-evidence")
    assert record is not None
    assert record["formal_issue_total"] == 0
    assert record["evidence"]["degraded_count"] == 1


def test_legacy_job_evidence_metrics_are_recomputed(sample_uploads: Path) -> None:
    """历史产物没有 evidence_completeness，指标要现场重算而不是留空。"""
    record = collect_job_record(sample_uploads / "job-legacy")
    assert record is not None
    assert record["evidence"]["source"] == "recomputed"
    assert record["evidence"]["total"] == 1
    assert record["evidence"]["complete"] == 0
    # 规则缺证据只告警、不降级，仍是正式问题
    assert record["evidence"]["rule_warning_count"] == 1
    assert record["formal_issue_total"] == 1


def test_build_report_summarizes_structural_metrics(sample_uploads: Path) -> None:
    report = build_report(sample_uploads)
    summary = report["summary"]

    assert summary["job_total"] == 5
    assert report["skipped_dirs"] == ["job-broken"]

    # 终态分布
    assert summary["status_distribution"]["done"]["count"] == 2
    assert summary["status_distribution"]["review_required"]["count"] == 2
    assert summary["status_distribution"]["error"]["count"] == 1
    assert summary["status_distribution"]["done"]["ratio"] == 0.4

    # 覆盖率分布：1.0 / 0.4 / 0.9 / 0.0，job-legacy 没有覆盖率数据
    coverage = summary["page_coverage"]
    assert coverage["measured_jobs"] == 4
    assert coverage["unmeasured_jobs"] == 1
    assert coverage["min"] == 0.0
    assert coverage["max"] == 1.0
    assert coverage["buckets"]["eq_1.0"]["count"] == 1
    assert coverage["buckets"]["0.8_to_1.0"]["count"] == 1
    assert coverage["buckets"]["lt_0.5"]["count"] == 2

    # 扫描页任务占比：仅 job-review
    assert summary["scanned_page_jobs"] == {"count": 1, "ratio": 0.2}
    # unknown 类型：仅 job-review
    assert summary["unknown_report_kind"] == {"count": 1, "ratio": 0.2}
    # 年份未识别：仅 job-review
    assert summary["unresolved_report_year"] == {"count": 1, "ratio": 0.2}

    # 空 findings：以"分析跑完"的任务为分母（done/degraded/review_required = 4 个），
    # 其中 job-review 与 job-degraded-evidence 正式问题为 0
    empty = summary["empty_findings_jobs"]
    assert empty["completed_jobs"] == 4
    assert empty["count"] == 2
    assert empty["ratio"] == 0.5

    # 证据完整率：3 条 finding（done 1 完整、degraded 1 缺证据、legacy 1 缺证据）
    evidence = summary["evidence_completeness"]
    assert evidence["findings_total"] == 3
    assert evidence["findings_complete"] == 1
    assert evidence["completeness_rate"] == 0.3333
    assert evidence["degraded_total"] == 1
    assert evidence["rule_warning_total"] == 1

    # report_id 唯一性：report-1 被两个不同 checksum 的任务共用 -> 冲突
    uniqueness = summary["report_id_uniqueness"]
    assert uniqueness["jobs_with_report_id"] == 4
    assert uniqueness["distinct_report_ids"] == 3
    assert uniqueness["unique"] is False
    assert uniqueness["collision_count"] == 1
    assert uniqueness["collisions"][0]["report_id"] == "report-1"
    assert uniqueness["collisions"][0]["job_ids"] == ["job-done", "job-legacy"]


def test_report_id_uniqueness_passes_without_collision(sample_uploads: Path) -> None:
    """对照：去掉撞车的历史任务后，唯一性检查必须通过。"""
    (sample_uploads / "job-legacy" / "status.json").unlink()
    (sample_uploads / "job-legacy").rmdir()

    summary = build_report(sample_uploads)["summary"]
    assert summary["report_id_uniqueness"]["unique"] is True
    assert summary["report_id_uniqueness"]["collision_count"] == 0


def test_summarize_handles_empty_input() -> None:
    summary = summarize([])
    assert summary["job_total"] == 0
    assert summary["page_coverage"]["mean"] is None
    assert summary["evidence_completeness"]["completeness_rate"] == 1.0
    assert summary["report_id_uniqueness"]["unique"] is True


def _snapshot_tree(root: Path) -> Dict[str, tuple]:
    snapshot: Dict[str, tuple] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            data = path.read_bytes()
            stat = path.stat()
            snapshot[path.relative_to(root).as_posix()] = (
                hashlib.sha256(data).hexdigest(),
                stat.st_mtime_ns,
            )
        else:
            snapshot[path.relative_to(root).as_posix() + "/"] = ("dir", 0)
    return snapshot


def test_replay_does_not_modify_existing_artifacts(
    sample_uploads: Path, tmp_path: Path
) -> None:
    """只读保证：跑完之后产物目录的内容与修改时间必须完全一致。"""
    before = _snapshot_tree(sample_uploads)

    output = tmp_path / "reports" / "replay.json"
    exit_code = main(
        ["--uploads", str(sample_uploads), "--output", str(output)]
    )

    assert exit_code == 0
    assert output.is_file()
    assert _snapshot_tree(sample_uploads) == before


def test_cli_writes_json_report(sample_uploads: Path, tmp_path: Path) -> None:
    output = tmp_path / "out" / "replay.json"
    assert main(["--uploads", str(sample_uploads), "--output", str(output)]) == 0

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["mode"] == "offline_metric_replay"
    assert report["summary"]["job_total"] == 5
    assert len(report["jobs"]) == 5
    # 局限必须写在报告里，避免被误读成"业务召回率达标"
    assert any("Golden Corpus" in text for text in report["limitations"])


def test_cli_refuses_to_write_into_uploads(sample_uploads: Path) -> None:
    """反例：默认只读语义要求报告不能落在被扫描的产物目录里。"""
    output = sample_uploads / "replay.json"
    assert main(["--uploads", str(sample_uploads), "--output", str(output)]) == 2
    assert not output.exists()


def test_cli_reports_missing_uploads_dir(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    assert main(["--uploads", str(missing), "--output", str(tmp_path / "r.json")]) == 2


def test_cli_limit_and_no_jobs(sample_uploads: Path, tmp_path: Path) -> None:
    output = tmp_path / "replay-limited.json"
    assert (
        main(
            [
                "--uploads",
                str(sample_uploads),
                "--output",
                str(output),
                "--limit",
                "2",
                "--no-jobs",
            ]
        )
        == 0
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["summary"]["job_total"] == 2
    assert "jobs" not in report
