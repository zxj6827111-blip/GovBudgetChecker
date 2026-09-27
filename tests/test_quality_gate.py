"""任务级质量门禁与 review_required 测试（Task 3，B-03 + P0-04/05 收尾）。

断言意图：
1. 门禁全过且有问题 -> done + findings_detected；
2. 门禁全过且无问题 -> done + no_findings（可与"没查完"区分，这是 P0-05 的核心）；
3. 覆盖率低 / 有扫描页 / report_kind=unknown / 年份为 None / AI 必需却失败
   -> review_required + incomplete，且带上可读原因码；
4. AI 降级但门禁通过 -> degraded，结论仍然有效（保留原语义）；
5. 端到端：合成"空文本页"PDF 走完流水线得到 review_required；正常件得到 done；
6. 规则执行失败仍然是 error（不被门禁改写）。
"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from api import main as pipeline_mod
from api import runtime
from api.main import _count_result_findings, _evaluate_quality_gate
from src.engine import check_obligations

GOOD_PAGES = {
    "page_count": 3,
    "text_page_count": 3,
    "low_text_pages": [],
    "low_text_page_count": 0,
    "scanned_pages": [],
    "scanned_page_count": 0,
    "page_coverage": 1.0,
}

# 规则执行摘要桩：所有适用规则已执行且无未决项（GPT5.6 P0-2 后，
# no_findings 必须有"规则已全部执行"的证据，摘要缺失会被判
# rules_not_executed 转人工复核）。
FULLY_EXECUTED_SUMMARY = {
    "total_rules": 10,
    "executed": 10,
    "pass": 10,
    "fail": 0,
    "not_applicable": 0,
    "insufficient_data": 0,
    "parse_error": 0,
    "execution_error": 0,
    "unresolved_total": 0,
    "failed_rules": [],
    "unresolved_rules": [],
}


def _reason_codes(gate):
    return [reason["code"] for reason in gate["review_reasons"]]


def test_gate_pass_with_findings_yields_done_and_findings_detected():
    gate = _evaluate_quality_gate(
        page_assessment=GOOD_PAGES,
        report_kind="budget",
        report_year=2025,
        ai_requested=True,
        ai_degraded=False,
        issue_total=4,
    )
    assert gate["status"] == "done"
    assert gate["quality_status"] == "complete"
    assert gate["analysis_conclusion"] == "findings_detected"
    assert gate["review_reasons"] == []


def test_gate_pass_without_findings_yields_no_findings_not_bare_done():
    gate = _evaluate_quality_gate(
        page_assessment=GOOD_PAGES,
        report_kind="final",
        report_year=2024,
        ai_requested=True,
        ai_degraded=False,
        issue_total=0,
        rule_execution_summary=dict(FULLY_EXECUTED_SUMMARY),
    )
    assert gate["status"] == "done"
    assert gate["analysis_conclusion"] == "no_findings"


def test_scanned_pages_force_review_required():
    gate = _evaluate_quality_gate(
        page_assessment={
            "page_count": 4,
            "low_text_pages": [2, 3],
            "scanned_pages": [2, 3],
            "scanned_page_count": 2,
            "page_coverage": 0.5,
        },
        report_kind="budget",
        report_year=2025,
        ai_requested=False,
        ai_degraded=False,
        issue_total=0,
    )
    assert gate["status"] == "review_required"
    assert gate["quality_status"] == "review_required"
    assert gate["analysis_conclusion"] == "incomplete"
    assert "scanned_pages_detected" in _reason_codes(gate)
    assert "low_page_coverage" in _reason_codes(gate)


def test_low_coverage_without_fully_blank_pages_still_gated():
    # 有残缺文本层（非 0 字符）的页面：不算扫描页，但覆盖率不达标仍要转复核
    gate = _evaluate_quality_gate(
        page_assessment={
            "page_count": 10,
            "low_text_pages": [4, 5, 6],
            "scanned_pages": [],
            "scanned_page_count": 0,
            "page_coverage": 0.7,
        },
        report_kind="budget",
        report_year=2025,
        ai_requested=False,
        ai_degraded=False,
        issue_total=1,
    )
    assert gate["status"] == "review_required"
    assert _reason_codes(gate) == ["low_page_coverage"]
    assert gate["review_reasons"][0]["pages"] == [4, 5, 6]


@pytest.mark.parametrize("report_kind", ["unknown", "", None, "  UNKNOWN  "])
def test_unknown_report_kind_forces_review_required(report_kind):
    gate = _evaluate_quality_gate(
        page_assessment=GOOD_PAGES,
        report_kind=report_kind,
        report_year=2025,
        ai_requested=False,
        ai_degraded=False,
        issue_total=2,
    )
    assert gate["status"] == "review_required"
    assert "unknown_report_kind" in _reason_codes(gate)


def test_unknown_report_year_forces_review_required():
    gate = _evaluate_quality_gate(
        page_assessment=GOOD_PAGES,
        report_kind="budget",
        report_year=None,
        ai_requested=False,
        ai_degraded=False,
        issue_total=2,
    )
    assert gate["status"] == "review_required"
    assert "unknown_report_year" in _reason_codes(gate)


def test_ai_degraded_but_optional_keeps_degraded_with_valid_conclusion(monkeypatch):
    monkeypatch.delenv("AI_ASSIST_REQUIRED", raising=False)
    gate = _evaluate_quality_gate(
        page_assessment=GOOD_PAGES,
        report_kind="budget",
        report_year=2025,
        ai_requested=True,
        ai_degraded=True,
        issue_total=3,
    )
    assert gate["status"] == "degraded"
    assert gate["quality_status"] == "degraded"
    # degraded 的语义是"部分能力降级但结论有效"，结论不能塌成 incomplete
    assert gate["analysis_conclusion"] == "findings_detected"


def test_ai_required_and_failed_forces_review_required(monkeypatch):
    monkeypatch.setenv("AI_ASSIST_REQUIRED", "true")
    gate = _evaluate_quality_gate(
        page_assessment=GOOD_PAGES,
        report_kind="budget",
        report_year=2025,
        ai_requested=True,
        ai_degraded=True,
        issue_total=3,
    )
    assert gate["status"] == "review_required"
    assert "ai_assist_required_but_failed" in _reason_codes(gate)


def test_coverage_threshold_is_configurable(monkeypatch):
    assessment = {"page_count": 10, "low_text_pages": [1], "scanned_page_count": 0, "page_coverage": 0.9}
    monkeypatch.setenv("PAGE_COVERAGE_MIN_RATIO", "0.95")
    gated = _evaluate_quality_gate(assessment, "budget", 2025, False, False, 0)
    assert gated["status"] == "review_required"
    monkeypatch.setenv("PAGE_COVERAGE_MIN_RATIO", "0.85")
    passed = _evaluate_quality_gate(
        assessment, "budget", 2025, False, False, 0,
        rule_execution_summary=dict(FULLY_EXECUTED_SUMMARY),
    )
    assert passed["status"] == "done"


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"issues": {"all": [1, 2, 3]}}, 3),
        ({"issues": {"error": [1], "warn": [2, 3], "info": []}}, 3),
        ({"issues": []}, 0),
        ({"rule_findings": [1, 2], "ai_findings": [3]}, 3),
        ({}, 0),
        ("not-a-dict", 0),
    ],
)
def test_count_result_findings(result, expected):
    assert _count_result_findings(result) == expected


class _FakePdf:
    def __init__(self, page_count: int):
        self.pages = [object()] * page_count

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def _prepare_job(tmp_path: Path, name: str, status_payload: dict) -> Path:
    job_dir = tmp_path / name
    job_dir.mkdir()
    (job_dir / "source.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    runtime.write_json_file(job_dir / "status.json", status_payload)
    return job_dir


def _full_rule_receipt(report_kind: str = "budget"):
    """构造"全部适用规则均已执行且无未决项"的规则执行摘要。

    必须带上 ``rule_statuses``（逐规则回执）：检查义务台账要按规则编号
    逐条核对"这项检查到底跑没跑"，只有总数无法证明任何一条具体规则执行过。
    规则编号直接从真实注册表取，保证桩与生产契约同源；用假的编号会让
    桩"看起来通过"而台账里全是未执行。
    """
    rules = sorted(check_obligations.registered_rule_ids(report_kind))
    return {
        "total_rules": len(rules),
        "executed": len(rules),
        "pass": len(rules),
        "fail": 0,
        "not_applicable": 0,
        "insufficient_data": 0,
        "parse_error": 0,
        "execution_error": 0,
        "unresolved_total": 0,
        "failed_rules": [],
        "unresolved_rules": [],
        "rule_statuses": {code: "pass" for code in rules},
    }


def _patch_pipeline(monkeypatch, page_texts, issues_payload, report_kind="budget"):
    monkeypatch.setattr(
        pipeline_mod.pdfplumber, "open", lambda _path: _FakePdf(len(page_texts))
    )
    counter = {"index": 0}

    def _fake_text(_page):
        text = page_texts[counter["index"]]
        counter["index"] += 1
        return text

    monkeypatch.setattr(pipeline_mod, "_extract_visible_text_from_page", _fake_text)
    monkeypatch.setattr(pipeline_mod, "_extract_tables_from_page", lambda _page: [])
    monkeypatch.setattr(
        pipeline_mod,
        "run_rules_in_process",
        AsyncMock(
            return_value={
                "issues": issues_payload,
                # 与真实 run_rules_in_process 契约一致：规则执行摘要随 payload 返回
                "rule_execution_summary": _full_rule_receipt(report_kind),
            }
        ),
    )
    monkeypatch.setattr(
        pipeline_mod, "persist_analysis_job_snapshot", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        pipeline_mod,
        "run_structured_ingest",
        AsyncMock(return_value={"status": "skipped", "review_item_count": 0, "review_items": []}),
    )
    # 用真实 Settings 接口关掉双模式（禁止再用不兼容的字典式 settings.get mock：
    # 那种 mock 曾掩盖 settings.get("dual_mode.enabled") 恒为 False 的配置缺陷）
    monkeypatch.setattr(
        pipeline_mod.settings, "is_dual_mode_enabled", lambda: False
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AI_ASSIST_REQUIRED", raising=False)
    monkeypatch.delenv("PAGE_COVERAGE_MIN_RATIO", raising=False)
    monkeypatch.delenv("SCANNED_PAGE_MIN_CHARS", raising=False)


@pytest.mark.asyncio
async def test_pipeline_marks_scanned_document_as_review_required(tmp_path, monkeypatch):
    job_dir = _prepare_job(
        tmp_path,
        "job-scanned",
        {
            "status": "queued",
            "mode": "legacy",
            "use_local_rules": True,
            "use_ai_assist": False,
            "report_year": 2025,
            "report_kind": "budget",
        },
    )
    # 两页全空文本：典型扫描件
    _patch_pipeline(monkeypatch, ["", ""], {"all": [], "error": [], "warn": [], "info": []})

    await pipeline_mod._run_pipeline_inner(job_dir)

    payload = runtime.read_json_file(job_dir / "status.json", default={})
    assert payload["status"] == "review_required"
    assert payload["quality_status"] == "review_required"
    assert payload["analysis_conclusion"] == "incomplete"
    assert payload["page_coverage"] == 0.0
    assert payload["scanned_page_count"] == 2
    assert "scanned_pages_detected" in [r["code"] for r in payload["review_reasons"]]
    assert payload["stage"] == "完成（需人工复核）"


@pytest.mark.asyncio
async def test_pipeline_clean_document_with_unimplemented_checks_is_not_done(
    tmp_path, monkeypatch
):
    """干净材料 + 全部适用规则执行完毕，存在"尚未实现"义务时不能算"检查完整"。

    历史前提：预算口径曾有尚未实现的检查义务（"占 XX.XX"漏百分号、绩效
    阶段金额口径），它们不是"没问题"，而是"没查"。WP4-F/WP4-H 已把这些
    缺口逐条真实补齐（不删分母），因此用合成 pending 义务锁住同一门禁
    语义：只要清单里存在未实现项，门禁必须保留 review_required。
    """
    from dataclasses import replace as dc_replace

    pending_target = next(
        item
        for item in check_obligations.OBLIGATION_CATALOG
        if "budget" in item.report_kinds and not item.requires_ai
    )
    with_pending = dc_replace(
        pending_target,
        checkers_by_kind={},
        pending_checkers=("BUD-SYNTHETIC-GAP",),
        gap_note="合成缺口：验证未实现义务阻塞门禁",
    )
    monkeypatch.setattr(
        check_obligations,
        "OBLIGATION_CATALOG",
        tuple(
            with_pending if item is pending_target else item
            for item in check_obligations.OBLIGATION_CATALOG
        ),
        raising=True,
    )

    job_dir = _prepare_job(
        tmp_path,
        "job-clean",
        {
            "status": "queued",
            "mode": "legacy",
            "use_local_rules": True,
            "use_ai_assist": False,
            "report_year": 2025,
            "report_kind": "budget",
        },
    )
    full_text = "一般公共预算财政拨款支出预算表" * 10
    _patch_pipeline(
        monkeypatch, [full_text, full_text], {"all": [], "error": [], "warn": [], "info": []}
    )

    await pipeline_mod._run_pipeline_inner(job_dir)

    payload = runtime.read_json_file(job_dir / "status.json", default={})
    assert payload["status"] == "review_required"
    assert payload["analysis_conclusion"] == "incomplete"
    assert payload["page_coverage"] == 1.0
    codes = [r["code"] for r in payload["review_reasons"]]
    assert "check_obligations_incomplete" in codes

    coverage = payload["result"]["meta"]["obligation_coverage"]
    assert coverage["catalog_version"] == check_obligations.OBLIGATION_CATALOG_VERSION
    # "已识别为预算"不是未完成原因：画像已确定文种，不能把它记成缺口
    assert "profile_unresolved" not in (coverage["by_reason"] or {})
    # 规则侧已被完整回执排除（没有 not_executed / 取数不足 / 解析歧义），
    # 剩下的只有真实的实现缺口，以及"本次未启用 AI"的语义复核义务。
    assert set(coverage["by_reason"]) <= {"not_implemented", "ai_not_run"}
    assert coverage["by_reason"].get("not_implemented", 0) > 0
    # AI 语义义务未执行会如实记账，但不阻塞门禁（未配置为必需能力时），
    # 因此阻塞数应严格等于"尚未实现"的项数。
    assert coverage["blocking_total"] == coverage["by_reason"]["not_implemented"]
    assert coverage["unresolved_total"] >= coverage["blocking_total"]
    # 完成率不得因分母调整而虚高
    assert coverage["applicable_total"] > 0
    assert coverage["coverage_rate"] is not None
    assert coverage["coverage_rate"] < 1.0


@pytest.mark.asyncio
async def test_pipeline_clean_document_is_done_when_catalog_has_no_gap(
    tmp_path, monkeypatch
):
    """把"尚未实现"的义务从清单里去掉后，干净材料才允许出 done + no_findings。

    这条测试守住反向边界：门禁不是无条件转人工——只要应检查事项全部完成、
    无问题、页覆盖达标，仍然可以给出 done + no_findings。同时说明
    "没有未完成项"是靠补实现达到的，不是靠删分母达到的（本测试只改清单，
    不改任何计数逻辑）。
    """
    implemented_only = tuple(
        item
        for item in check_obligations.OBLIGATION_CATALOG
        if not item.pending_checkers and not item.requires_ai
    )
    monkeypatch.setattr(
        check_obligations, "OBLIGATION_CATALOG", implemented_only, raising=True
    )

    job_dir = _prepare_job(
        tmp_path,
        "job-clean-all-implemented",
        {
            "status": "queued",
            "mode": "legacy",
            "use_local_rules": True,
            "use_ai_assist": False,
            "report_year": 2025,
            "report_kind": "budget",
        },
    )
    full_text = "一般公共预算财政拨款支出预算表" * 10
    _patch_pipeline(
        monkeypatch, [full_text, full_text], {"all": [], "error": [], "warn": [], "info": []}
    )

    await pipeline_mod._run_pipeline_inner(job_dir)

    payload = runtime.read_json_file(job_dir / "status.json", default={})
    assert payload["status"] == "done"
    assert payload["quality_status"] == "complete"
    assert payload["analysis_conclusion"] == "no_findings"
    # "未发现问题"必须限定在已完成的检查范围内，而不是含混地说材料没问题
    assert payload["conclusion_scope"] == "no_findings_within_covered_checks"
    assert payload["review_reasons"] == []
    coverage = payload["result"]["meta"]["obligation_coverage"]
    assert coverage["blocking_total"] == 0
    assert coverage["coverage_rate"] == 1.0


@pytest.mark.asyncio
async def test_pipeline_unknown_report_kind_is_gated(tmp_path, monkeypatch):
    job_dir = _prepare_job(
        tmp_path,
        "job-unknown-kind",
        {
            "status": "queued",
            "mode": "legacy",
            "use_local_rules": True,
            "use_ai_assist": False,
            "report_year": 2025,
            "report_kind": "unknown",
        },
    )
    full_text = "公开材料说明" * 30
    _patch_pipeline(monkeypatch, [full_text], {"all": [{"id": "x"}], "error": [], "warn": [], "info": []})
    # 文件名不含预算/决算关键词，normalize_report_kind 仍会给 unknown
    monkeypatch.setattr(pipeline_mod.runtime, "normalize_report_kind", lambda *_a: "unknown")

    await pipeline_mod._run_pipeline_inner(job_dir)

    payload = runtime.read_json_file(job_dir / "status.json", default={})
    assert payload["status"] == "review_required"
    assert "unknown_report_kind" in [r["code"] for r in payload["review_reasons"]]


@pytest.mark.asyncio
async def test_pipeline_error_path_sets_analysis_error_conclusion(tmp_path, monkeypatch):
    from src.services.rule_process import RuleExecutionError

    job_dir = _prepare_job(
        tmp_path,
        "job-error",
        {
            "status": "queued",
            "mode": "legacy",
            "use_local_rules": True,
            "use_ai_assist": False,
            "report_year": 2025,
            "report_kind": "budget",
            # 模拟上一轮分析残留的"通过"字段，必须被错误态覆盖
            "quality_status": "complete",
            "analysis_conclusion": "no_findings",
        },
    )
    _patch_pipeline(monkeypatch, ["正文" * 100], {"all": []})
    monkeypatch.setattr(
        pipeline_mod,
        "run_rules_in_process",
        AsyncMock(side_effect=RuleExecutionError("rule crashed")),
    )

    await pipeline_mod._run_pipeline_inner(job_dir)

    payload = runtime.read_json_file(job_dir / "status.json", default={})
    assert payload["status"] == "error"
    assert payload["analysis_conclusion"] == "analysis_error"
    assert payload["quality_status"] == "review_required"
