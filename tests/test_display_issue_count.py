"""展示层问题计数口径测试（Task A：修复计数口径分裂）。

要验证的核心命题：列表/统计接口读到的问题数，必须与质量门禁使用的
`evidence_guard.count_formal_findings` 完全一致，否则会出现
"任务是 review_required / incomplete，部门统计却显示有 N 个问题"的矛盾。

正反对照：
- 反例（改动前会失败）：含降级项的产物，展示计数必须扣掉降级项；
  全部降级时展示计数必须为 0、`has_issues` 为 False。
- 正例（向后兼容）：历史产物没有 `evidence_status` 字段时，
  计数必须与改动前保持一致（一条不少）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from api import runtime
from api.routes.organizations import _extract_summary_issue_total
from src.services.evidence_guard import (
    EVIDENCE_STATUS_COMPLETE,
    EVIDENCE_STATUS_DEGRADED,
    count_formal_findings,
)


def _finding(
    finding_id: str,
    *,
    severity: str = "high",
    evidence_status: str | None = None,
) -> Dict[str, Any]:
    """构造一条 finding；`evidence_status=None` 用来模拟历史产物（无该字段）。"""
    item: Dict[str, Any] = {
        "id": finding_id,
        "source": "ai",
        "rule_id": "C-001",
        "severity": severity,
        "title": "示例问题",
        "message": "示例问题描述",
        "page_number": 3,
    }
    if evidence_status is not None:
        item["evidence_status"] = evidence_status
    return item


def _write_job(tmp_path: Path, job_id: str, result: Dict[str, Any], status: str) -> Path:
    job_dir = tmp_path / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "sample.pdf").write_bytes(b"%PDF-1.4 fake")
    payload = {
        "job_id": job_id,
        "status": status,
        "progress": 100,
        "filename": "sample.pdf",
        "result": result,
    }
    (job_dir / "status.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return job_dir


def _legacy_result(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """legacy 分桶结构：`all` 与 error/warn/info 共享同一批对象。"""
    return {
        "issues": {
            "all": items,
            "error": [item for item in items if item["severity"] in {"high", "critical"}],
            "warn": [item for item in items if item["severity"] in {"medium", "low"}],
            "info": [
                item
                for item in items
                if item["severity"] not in {"high", "critical", "medium", "low"}
            ],
        },
        "meta": {},
    }


def test_display_count_excludes_degraded_findings(tmp_path):
    """含降级项：展示计数必须与 count_formal_findings 一致，并单独暴露降级条数。"""
    items = [
        _finding("f-1", evidence_status=EVIDENCE_STATUS_COMPLETE),
        _finding("f-2", evidence_status=EVIDENCE_STATUS_DEGRADED, severity="manual_review"),
    ]
    result = _legacy_result(items)
    job_dir = _write_job(tmp_path, "job-mixed", result, "review_required")

    summary = runtime.collect_job_summary(job_dir)

    assert count_formal_findings(result) == 1
    assert summary["issue_total"] == 1
    assert summary["formal_issue_total"] == count_formal_findings(result)
    assert summary["degraded_issue_total"] == 1
    assert summary["has_issues"] is True
    # 降级项 severity 为 manual_review，会落进 info 桶；扣除后 info 必须为 0
    assert summary["issue_info"] == 0
    assert summary["issue_error"] == 1


def test_all_degraded_findings_report_zero_issues(tmp_path):
    """反例（改动前失败）：全部降级的任务不得再显示"有问题"。"""
    items = [
        _finding("f-1", evidence_status=EVIDENCE_STATUS_DEGRADED, severity="manual_review"),
        _finding("f-2", evidence_status=EVIDENCE_STATUS_DEGRADED, severity="manual_review"),
    ]
    result = _legacy_result(items)
    job_dir = _write_job(tmp_path, "job-all-degraded", result, "review_required")

    summary = runtime.collect_job_summary(job_dir)

    assert count_formal_findings(result) == 0
    assert summary["issue_total"] == 0
    assert summary["formal_issue_total"] == 0
    assert summary["degraded_issue_total"] == 2
    assert summary["has_issues"] is False
    # 组织/部门统计读的是同一份 summary，这里断言统计口径也归零
    assert _extract_summary_issue_total(summary) == 0
    # 终态语义不能被改动影响：review_required 仍然是 incomplete
    assert summary["analysis_conclusion"] == "incomplete"


def test_legacy_result_without_evidence_status_counts_unchanged(tmp_path):
    """正例（向后兼容）：历史产物没有 evidence_status 字段，计数与改动前一致。"""
    items = [_finding("f-1"), _finding("f-2", severity="medium")]
    result = _legacy_result(items)
    job_dir = _write_job(tmp_path, "job-legacy", result, "done")

    summary = runtime.collect_job_summary(job_dir)

    assert count_formal_findings(result) == 2
    assert summary["issue_total"] == 2
    assert summary["formal_issue_total"] == 2
    assert summary["degraded_issue_total"] == 0
    assert summary["issue_error"] == 1
    assert summary["issue_warn"] == 1
    assert summary["has_issues"] is True
    assert _extract_summary_issue_total(summary) == 2


def test_dual_mode_merged_total_excludes_degraded(tmp_path):
    """双模式：merged 计数也要扣掉降级项，否则部门统计仍会显示"有问题"。"""
    ai_findings = [
        _finding("ai-1", evidence_status=EVIDENCE_STATUS_DEGRADED, severity="manual_review"),
    ]
    rule_findings = [
        _finding("rule-1", evidence_status=EVIDENCE_STATUS_COMPLETE),
    ]
    result = {
        "ai_findings": ai_findings,
        "rule_findings": rule_findings,
        "merged": {
            "totals": {"merged": 2, "conflicts": 0, "agreements": 0},
            "merged_ids": ["ai-1", "rule-1"],
        },
        "meta": {"mode": "dual", "dual_mode_enabled": True},
    }
    job_dir = _write_job(tmp_path, "job-dual", result, "review_required")

    summary = runtime.collect_job_summary(job_dir)

    assert count_formal_findings(result) == 1
    assert summary["merged_issue_total"] == 1
    assert summary["ai_issue_total"] == 0
    assert summary["local_issue_total"] == 1
    assert summary["degraded_issue_total"] == 1
    assert _extract_summary_issue_total(summary) == 1


def test_dual_mode_merged_total_unchanged_for_legacy_payload(tmp_path):
    """向后兼容对照：同样的双模式结构，缺 evidence_status 时 merged 计数不变。"""
    result = {
        "ai_findings": [_finding("ai-1")],
        "rule_findings": [_finding("rule-1")],
        "merged": {
            "totals": {"merged": 2, "conflicts": 0, "agreements": 0},
            "merged_ids": ["ai-1", "rule-1"],
        },
        "meta": {"mode": "dual", "dual_mode_enabled": True},
    }
    job_dir = _write_job(tmp_path, "job-dual-legacy", result, "done")

    summary = runtime.collect_job_summary(job_dir)

    assert summary["merged_issue_total"] == 2
    assert summary["ai_issue_total"] == 1
    assert summary["local_issue_total"] == 1
    assert summary["degraded_issue_total"] == 0
