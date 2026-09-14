"""Task 15.2 / 缺口 P2-06 + P2-03：CI 业务门禁与迁移步骤。

整改前的事实：`.github/workflows/ci.yml` 只有 Ruff / Mypy / Pytest / Frontend build / E2E，
**没有迁移步骤、也没有任何业务指标门禁**。于是"测试全绿"只能说明代码没抛异常，
既不能说明迁移能从空库跑起来，也不能说明结果里没有虚假成功。

断言意图（每条阈值都有反例，杜绝恒真门禁）：
1. `tests/fixtures/replay/pass` 语料必须五项全绿；
2. 5 个 `fail_*` 语料**各自只破坏一个维度**，必须精确触发对应的那一条检查失败
   —— 这证明每条阈值都真的在起作用，而不是写了不判；
3. 阈值可调：把阈值拧紧后，原本通过的语料必须变红（证明阈值是被读取的参数，
   不是硬编码的常量）；
4. 数据缺失时的行为：`--allow-missing` 才跳过并明确说明，否则失败
   —— 避免"CI 上没数据 => 门禁静默通过"这种假绿。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Dict, List

import pytest

from scripts.check_replay_thresholds import (
    check_evidence_completeness,
    evaluate,
    load_report,
    main as gate_main,
)
from scripts.run_db_migrations import main as migrations_main

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIXTURES = _REPO_ROOT / "tests" / "fixtures" / "replay"


def _results(corpus: str) -> Dict[str, bool]:
    report = load_report(report_path=None, uploads=str(_FIXTURES / corpus))
    assert report is not None, f"fixture 语料缺失：{corpus}"
    return {item.name: item.passed for item in evaluate(report)}


def test_pass_corpus_satisfies_every_check() -> None:
    results = _results("pass")
    assert results == {
        "replay_integrity": True,
        "report_id_uniqueness": True,
        "completed_jobs_have_page_coverage": True,
        "done_jobs_min_page_coverage": True,
        "evidence_completeness_rate": True,
        "unknown_report_kind_ratio": True,
    }


@pytest.mark.parametrize(
    ("corpus", "expected_failure"),
    [
        ("fail_report_id_collision", "report_id_uniqueness"),
        ("fail_missing_coverage", "completed_jobs_have_page_coverage"),
        ("fail_low_coverage_done", "done_jobs_min_page_coverage"),
        ("fail_low_evidence", "evidence_completeness_rate"),
        ("fail_high_unknown_ratio", "unknown_report_kind_ratio"),
    ],
)
def test_each_threshold_has_a_failing_corpus(corpus: str, expected_failure: str) -> None:
    """每条阈值都必须能被单独触发；顺带断言没有连带误伤其它检查。"""
    results = _results(corpus)
    failed: List[str] = [name for name, passed in results.items() if not passed]
    assert failed == [expected_failure], f"{corpus} 预期只触发 {expected_failure}，实际 {failed}"


def test_pass_corpus_fails_when_thresholds_are_tightened() -> None:
    """阈值必须是真参数：拧紧后同一份语料要变红。

    pass 语料里有一个覆盖率 0.95 的 done 任务，把下限提到 0.99 就该被拦。
    """
    report = load_report(report_path=None, uploads=str(_FIXTURES / "pass"))
    assert report is not None

    baseline = {item.name: item.passed for item in evaluate(report)}
    assert baseline["done_jobs_min_page_coverage"] is True

    tightened = {item.name: item.passed for item in evaluate(report, min_page_coverage=0.99)}
    assert tightened["done_jobs_min_page_coverage"] is False

    # 证据完整率同理
    assert {item.name: item.passed for item in evaluate(report, min_evidence_rate=1.01)}[
        "evidence_completeness_rate"
    ] is False

    # unknown 比例同理（pass 语料里 1/4 是 unknown）
    assert {item.name: item.passed for item in evaluate(report, max_unknown_kind_ratio=0.1)}[
        "unknown_report_kind_ratio"
    ] is False


def test_missing_data_source_requires_explicit_allow_missing(tmp_path, capsys) -> None:
    absent = tmp_path / "no-such-uploads"

    # 反例：没给 --allow-missing 时必须失败，不能静默通过
    assert gate_main(["--uploads", str(absent)]) == 2

    # 正例：显式允许跳过时才返回 0，且必须打印"这不代表质量达标"
    assert gate_main(["--uploads", str(absent), "--allow-missing"]) == 0
    output = capsys.readouterr().out
    assert "SKIP" in output
    assert "不代表业务质量达标" in output


@pytest.mark.parametrize(
    "option_value",
    [
        ("--min-page-coverage", "nan"),
        ("--min-page-coverage", "-inf"),
        ("--min-evidence-rate", "-0.1"),
        ("--max-unknown-kind-ratio", "1.1"),
    ],
)
def test_cli_rejects_invalid_thresholds(option_value) -> None:
    option, value = option_value
    with pytest.raises(SystemExit) as exc_info:
        gate_main(["--uploads", str(_FIXTURES / "pass"), option, value])
    assert exc_info.value.code == 2


def test_gate_cli_handles_non_mapping_summary_without_crashing(tmp_path, capsys) -> None:
    report_path = tmp_path / "malformed.json"
    report_path.write_text(json.dumps({"summary": "not-a-mapping", "jobs": []}), encoding="utf-8")

    assert gate_main(["--report", str(report_path), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is False
    assert payload["job_total"] is None


def test_gate_reports_limitation_in_human_output(capsys) -> None:
    """门禁输出必须自带"不度量召回率"的免责说明，避免被误读成业务质量达标。"""
    assert gate_main(["--uploads", str(_FIXTURES / "pass")]) == 0
    output = capsys.readouterr().out
    assert "Golden Corpus" in output
    assert "召回率" in output


def test_malformed_or_empty_report_fails_closed() -> None:
    """空报告与缺失结构不能把结构性门禁变成假绿。"""
    empty = {"summary": {}, "jobs": []}
    results = {item.name: item.passed for item in evaluate(empty)}
    assert not all(results.values())
    assert results["report_id_uniqueness"] is False
    assert results["completed_jobs_have_page_coverage"] is False
    assert results["evidence_completeness_rate"] is False
    assert results["unknown_report_kind_ratio"] is False

    valid = load_report(report_path=None, uploads=str(_FIXTURES / "pass"))
    assert valid is not None

    without_evidence = copy.deepcopy(valid)
    del without_evidence["summary"]["evidence_completeness"]
    assert {item.name: item.passed for item in evaluate(without_evidence)}[
        "evidence_completeness_rate"
    ] is False

    without_unknown = copy.deepcopy(valid)
    del without_unknown["summary"]["unknown_report_kind"]
    assert {item.name: item.passed for item in evaluate(without_unknown)}[
        "unknown_report_kind_ratio"
    ] is False

    without_jobs = copy.deepcopy(valid)
    del without_jobs["jobs"]
    assert {item.name: item.passed for item in evaluate(without_jobs)}[
        "completed_jobs_have_page_coverage"
    ] is False

    malformed_jobs = copy.deepcopy(valid)
    malformed_jobs["jobs"] = [{}]
    assert {item.name: item.passed for item in evaluate(malformed_jobs)}[
        "completed_jobs_have_page_coverage"
    ] is False


def test_report_id_coverage_is_required_even_without_collisions() -> None:
    report = load_report(report_path=None, uploads=str(_FIXTURES / "pass"))
    assert report is not None
    report["summary"]["report_id_uniqueness"].update(
        {
            "total_jobs": 4,
            "jobs_with_report_id": 3,
            "jobs_without_report_id": 1,
            "identity_complete": False,
        }
    )

    result = {item.name: item.passed for item in evaluate(report)}
    assert result["report_id_uniqueness"] is False


@pytest.mark.parametrize("invalid_ratio", [float("nan"), float("inf"), -0.1, 1.1, True])
def test_ratio_checks_reject_non_finite_out_of_range_and_boolean_values(
    invalid_ratio,
) -> None:
    report = load_report(report_path=None, uploads=str(_FIXTURES / "pass"))
    assert report is not None
    report["summary"]["unknown_report_kind"]["ratio"] = invalid_ratio
    result = {item.name: item.passed for item in evaluate(report)}["unknown_report_kind_ratio"]
    assert result is False


def test_replay_integrity_rejects_skipped_directories_and_count_mismatch() -> None:
    report = load_report(report_path=None, uploads=str(_FIXTURES / "pass"))
    assert report is not None

    report["skipped_count"] = 1
    report["skipped_dirs"] = ["broken-job"]
    assert {item.name: item.passed for item in evaluate(report)}["replay_integrity"] is False

    report["skipped_count"] = 0
    report["skipped_dirs"] = ["stale-entry"]
    assert {item.name: item.passed for item in evaluate(report)}["replay_integrity"] is False


def test_replay_integrity_recomputes_summary_from_job_rows() -> None:
    """摘要被篡改时，不能仅因 jobs 数量仍相等就让报告假绿。"""
    report = load_report(report_path=None, uploads=str(_FIXTURES / "pass"))
    assert report is not None
    report["summary"]["unknown_report_kind"]["count"] = 0
    report["summary"]["unknown_report_kind"]["ratio"] = 0.0

    result = {item.name: item for item in evaluate(report)}["replay_integrity"]
    assert result.passed is False
    assert "summary.unknown_report_kind" in result.detail


def test_ratio_checks_reject_inconsistent_counts_and_rates() -> None:
    report = load_report(report_path=None, uploads=str(_FIXTURES / "pass"))
    assert report is not None

    report["summary"]["unknown_report_kind"]["ratio"] = 0.0
    assert {item.name: item.passed for item in evaluate(report)}["unknown_report_kind_ratio"] is False

    report = load_report(report_path=None, uploads=str(_FIXTURES / "pass"))
    assert report is not None
    evidence = report["summary"]["evidence_completeness"]
    evidence["findings_complete"] = evidence["findings_total"] + 1
    assert {item.name: item.passed for item in evaluate(report)}["evidence_completeness_rate"] is False


def test_evidence_gate_uses_locatable_rate_and_falls_back() -> None:
    """B1 口径：门禁只看可定位类完整率，文档级单列不进分母；旧报告回退全量口径。

    场景一（新口径）：全量完整率 0.5（被 96 条 BUD-001 文档级 finding 拖低），
    但可定位类 4/4 全完整——门禁必须绿；
    场景二（回退）：旧报告没有可定位类字段，全量完整率 0.5——门禁必须红
    （回退不能把历史报告的已知问题放行）；
    场景三（纯文档级）：只有 BUD-001 类 finding、可定位分母为 0——按"没有
    可判定的可定位样本"跳过，而不是把天然无页码的问题算成证据缺口。
    """
    new_format = {
        "summary": {
            "evidence_completeness": {
                "findings_total": 100,
                "findings_complete": 50,
                "completeness_rate": 0.5,
                "locatable_findings_total": 4,
                "locatable_findings_complete": 4,
                "locatable_completeness_rate": 1.0,
                "document_level_findings_total": 96,
            }
        }
    }
    assert {i.name: i.passed for i in evaluate(new_format)}["evidence_completeness_rate"] is True

    legacy_format = {
        "summary": {
            "evidence_completeness": {
                "findings_total": 100,
                "findings_complete": 50,
                "completeness_rate": 0.5,
            }
        }
    }
    assert {i.name: i.passed for i in evaluate(legacy_format)}[
        "evidence_completeness_rate"
    ] is False

    document_level_only = {
        "summary": {
            "evidence_completeness": {
                "findings_total": 96,
                "findings_complete": 0,
                "completeness_rate": 0.0,
                "locatable_findings_total": 0,
                "locatable_findings_complete": 0,
                "locatable_completeness_rate": None,
                "document_level_findings_total": 96,
            }
        }
    }
    assert {i.name: i.passed for i in evaluate(document_level_only)}[
        "evidence_completeness_rate"
    ] is True

    missing_zero_rate = {
        "summary": {
            "evidence_completeness": {
                "findings_total": 0,
            }
        }
    }
    assert check_evidence_completeness(missing_zero_rate, 0.99).passed is False


def test_migration_script_requires_database_url(capsys) -> None:
    """迁移脚本的数据源缺失行为与业务门禁保持一致。

    注意：`tests/conftest.py` 的 autouse fixture 会摘掉 `DATABASE_URL`，
    所以这里天然处于"未配置"状态，不会连真实库。
    """
    assert migrations_main([]) == 2
    assert migrations_main(["--allow-missing"]) == 0
    assert "SKIP" in capsys.readouterr().out
