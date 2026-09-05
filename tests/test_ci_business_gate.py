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

from pathlib import Path
from typing import Dict, List

import pytest

from scripts.check_replay_thresholds import (
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

    tightened = {
        item.name: item.passed for item in evaluate(report, min_page_coverage=0.99)
    }
    assert tightened["done_jobs_min_page_coverage"] is False

    # 证据完整率同理
    assert {
        item.name: item.passed for item in evaluate(report, min_evidence_rate=1.01)
    }["evidence_completeness_rate"] is False

    # unknown 比例同理（pass 语料里 1/4 是 unknown）
    assert {
        item.name: item.passed for item in evaluate(report, max_unknown_kind_ratio=0.1)
    }["unknown_report_kind_ratio"] is False


def test_missing_data_source_requires_explicit_allow_missing(tmp_path, capsys) -> None:
    absent = tmp_path / "no-such-uploads"

    # 反例：没给 --allow-missing 时必须失败，不能静默通过
    assert gate_main(["--uploads", str(absent)]) == 2

    # 正例：显式允许跳过时才返回 0，且必须打印"这不代表质量达标"
    assert gate_main(["--uploads", str(absent), "--allow-missing"]) == 0
    output = capsys.readouterr().out
    assert "SKIP" in output
    assert "不代表业务质量达标" in output


def test_gate_reports_limitation_in_human_output(capsys) -> None:
    """门禁输出必须自带"不度量召回率"的免责说明，避免被误读成业务质量达标。"""
    assert gate_main(["--uploads", str(_FIXTURES / "pass")]) == 0
    output = capsys.readouterr().out
    assert "Golden Corpus" in output
    assert "召回率" in output


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
    assert {
        i.name: i.passed for i in evaluate(legacy_format)
    }["evidence_completeness_rate"] is False

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
    assert {
        i.name: i.passed for i in evaluate(document_level_only)
    }["evidence_completeness_rate"] is True


def test_migration_script_requires_database_url(capsys) -> None:
    """迁移脚本的数据源缺失行为与业务门禁保持一致。

    注意：`tests/conftest.py` 的 autouse fixture 会摘掉 `DATABASE_URL`，
    所以这里天然处于"未配置"状态，不会连真实库。
    """
    assert migrations_main([]) == 2
    assert migrations_main(["--allow-missing"]) == 0
    assert "SKIP" in capsys.readouterr().out
