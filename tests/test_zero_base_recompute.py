"""CMM-007：同比基期/本期/增减额与增长百分比的确定性复算（零基数）。

真值来源（冻结，不得改来迁就实现）
--------------------------------
两条真实漏报，均为 2026-09-17 独立验收登记反例（outputs/plan_independent_
review_20260917/独立验收报告.md），本会话追溯回 PDF 原文逐字核实：

- **Truth A（宜川）**：宜川路街道 2025 年度决算（41 页，SHA ``f809eef2…``）
  P36「七、财政拨款"三公"经费支出决算情况说明」：本期 0.3 万元、
  比上年增加 0.3 万元，却写「增长 100%」→ 复算基期 0.3−0.3=0，
  同比百分比无定义。同页其余同比句在仓库统一容差内自洽（车辆行复算
  15.51% vs 声明 15.31%，差 0.20pp < 0.5pp 不报）→ 全文档恰好 1 条 finding。
- **Truth B（文旅）**：文化和旅游局 2025 年度部门决算（33 页，SHA
  ``74e6afd0…``，新冻结夹具 SHA 双锁）P28 同节：本期 0.40 万元、增加
  0.40 万元、「增长 100%」→ 基期 0。同页其余同比句完全自洽
  （总额 86.61% ✓、车辆 88.37% ✓、出国持平无数值）→ 恰好 1 条 finding。

夹具口径（诚实声明）：两条零基数真值是 **REAL**；预算零基数路径与
负例 A~J 用 **CONTRACT FIXTURE**（合成，非真实样张）。
百分比容差复用仓库统一策略（BUD-111/V33-234 同款 abs 0.5pp / 相对 1%），
金额侧 Decimal + 显示半步长包络，不自造容差。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.engine.check_obligations import (  # noqa: E402
    OBLIGATION_NOT_IMPLEMENTED,
    build_obligation_ledger,
    registered_rule_ids,
)
from src.engine.pipeline import run_rules_with_outcomes  # noqa: E402
from src.engine.rule_outcome import STATUS_FAIL, RuleDeferred  # noqa: E402
from src.engine.rules_v33 import build_document  # noqa: E402
from src.engine.common_rules import CMM007_ZeroBaseTrendRecompute  # noqa: E402

RULE_ID = "CMM-007"
OBLIGATION_ID = "OBL-TREND-ZERO-BASE"

#: 宜川路街道 2025 年度决算（Truth A；与 WP4-A/B/C 共用冻结夹具，SHA 双锁）
YICHUAN_FIXTURE = ROOT / "tests" / "fixtures" / "cross_san_gong_truth_page_data.json"
YICHUAN_SOURCE_SHA = "f809eef2afa91d258d5fbe4b0d1553dbe3a8ef9c4688d07e3a73aeaecd02ec03"
YICHUAN_FIXTURE_SHA = "45d761f3e3b7dc2ac023f1224bf4f02f7496860cca33b9f925ac8da991dcab4c"

#: 文化和旅游局 2025 年度部门决算（Truth B；生产管线同口径提取冻结）
WENLV_FIXTURE = ROOT / "tests" / "fixtures" / "wenlv_narrative_truth_page_data.json"
WENLV_SOURCE_SHA = "74e6afd06669098f57391a13dad63a6f2a15cac620ac9f32018e79649fe6a367"
WENLV_FIXTURE_SHA = "07813ae61b04cf9f3afd2bf93c69b357ff9aeefee3ba6a893b34d77d956e8252"

SANGONG_SECTION = "七、财政拨款“三公”经费支出决算情况说明"


# ---------------------------------------------------------------------------
# 夹具装载（fail-closed：缺失或不符即失败，不静默跳过）
# ---------------------------------------------------------------------------


def _load_fixture(path: Path, source_sha: str, fixture_sha: str) -> Dict[str, Any]:
    assert path.exists(), f"固定夹具缺失: {path}"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == fixture_sha, (
        f"夹具内容哈希不符（{path.name}）——夹具被修改或需重新生成"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["source_pdf_sha256"] == source_sha, f"夹具源 PDF SHA 不符（{path.name}）"
    return payload


@pytest.fixture(scope="module")
def yichuan() -> Dict[str, Any]:
    return _load_fixture(YICHUAN_FIXTURE, YICHUAN_SOURCE_SHA, YICHUAN_FIXTURE_SHA)


@pytest.fixture(scope="module")
def wenlv() -> Dict[str, Any]:
    return _load_fixture(WENLV_FIXTURE, WENLV_SOURCE_SHA, WENLV_FIXTURE_SHA)


def _document(payload: Dict[str, Any]) -> Any:
    return build_document(
        path=f"{payload.get('doc_id', 'sample')}.pdf",
        page_texts=list(payload["page_texts"]),
        page_tables=json.loads(json.dumps(payload.get("page_tables", []))),
        filesize=0,
    )


def _run(doc: Any) -> List[Any]:
    return list(CMM007_ZeroBaseTrendRecompute().apply(doc))


def _contract_doc(*sections: str) -> Any:
    """合成 contract 文档：页 1 提供年度锚，其后每参量一页。"""
    return build_document(
        path="contract-zero-base.pdf",
        page_texts=["某某局 2025 年度部门决算公开", *sections],
        page_tables=[],
        filesize=0,
    )


def _final_page(*clauses: str) -> str:
    """把合同用例放进同一个决算说明章节（同节才允许指标取数）。"""
    return "三、支出决算情况说明\n" + "\n".join(clauses)


def _zero_hits(issues: List[Any]) -> List[Any]:
    return [i for i in issues if (getattr(i, "location", None) or {}).get("zero_base")]


# ---------------------------------------------------------------------------
# 真实 truth 正例
# ---------------------------------------------------------------------------


def test_truth_a_yichuan_reports_exactly_the_zero_base_conflict(yichuan):
    """Truth A：宜川 P36 接待费 0.3/增加0.3/增长100% → 恰好 1 条 error finding。

    同页其余同比句在仓库统一容差内自洽（车辆 15.51% vs 15.31% 差 0.20pp
    < 0.5pp 不报）——本测试同时锁死"不误报"。
    """
    issues = _run(_document(yichuan))

    assert len(issues) == 1, [getattr(i, "message", "") for i in issues]
    issue = issues[0]
    assert issue.rule == RULE_ID
    assert issue.severity == "error", "零基数百分比属明确数学口径错误，不得降级"

    location = issue.location
    assert location["indicator"] == "公务接待费"
    assert location["current_amount"] == "0.3"
    assert location["direction"] == "增加"
    assert location["delta_amount"] == "0.3"
    assert location["derived_prior"] == "0.00"
    assert location["declared_percent"] == "100"
    assert location["unit"] == "万元"
    assert location["fiscal_year"] == 2025
    assert location["obligation_id"] == OBLIGATION_ID
    assert location["zero_base"] is True
    assert location["page"] == 36
    assert location["section"] == SANGONG_SECTION
    refs = {ref["role"]: ref for ref in location["table_refs"]}
    assert "增加0.3万元" in refs["增减句"]["span"]
    assert refs["增减句"]["page"] == 36
    assert "不存在有限定义" in issue.message


def test_truth_b_wenlv_reports_exactly_one_finding(wenlv):
    """Truth B：文旅 P28 接待费 0.40/增加0.40/增长100% → 恰好 1 条 error。

    同页其余同比句（总额 86.61%、车辆 88.37%）完全自洽——锁死不误报。
    """
    issues = _run(_document(wenlv))

    assert len(issues) == 1, [getattr(i, "message", "") for i in issues]
    issue = issues[0]
    assert issue.severity == "error"

    location = issue.location
    assert location["indicator"] == "公务接待费"
    assert location["current_amount"] == "0.40"
    assert location["delta_amount"] == "0.40"
    assert location["derived_prior"] == "0.00"
    assert location["declared_percent"] == "100"
    assert location["page"] == 28
    assert location["section"] == SANGONG_SECTION


def test_truth_pipeline_reports_zero_base_in_full_final_run(wenlv):
    """真实 truth pipeline：完整 final 规则集执行必须产出本规则的 fail 结论。

    Mutation C 的守卫：把 CMM-007 从注册表拿掉后这条必须红。
    """
    doc = _document(wenlv)
    issues, outcomes = run_rules_with_outcomes(doc, False, report_kind="final")

    assert RULE_ID in registered_rule_ids("final")
    rule_codes = {outcome.rule_id for outcome in outcomes}
    assert RULE_ID in rule_codes, "final 规则集未执行 CMM-007——注册表与执行集脱节"
    outcome = next(item for item in outcomes if item.rule_id == RULE_ID)
    assert outcome.status == STATUS_FAIL
    assert any(
        issue.rule == RULE_ID and "增长" in issue.message for issue in issues
    ), "完整 final 执行未保留零基数 finding（Contract C1：fail 的 finding 不得被丢弃）"


# ---------------------------------------------------------------------------
# budget+final 双注册与预算执行路径
# ---------------------------------------------------------------------------


def test_rule_in_both_registries():
    """任务 §二十五/三十二：CMM-007 是通用规则，budget 与 final 都必须包含。"""
    assert RULE_ID in registered_rule_ids("final")
    assert RULE_ID in registered_rule_ids("budget")


def test_budget_contract_zero_base_executes_under_budget_kind():
    """预算 contract（CONTRACT FIXTURE，非真实样张）：本年预算 50、比上年预算
    增加 50、增长100% → 零基数 finding，且 budget 管线真的执行了 CMM-007。"""
    doc = _contract_doc(
        "二、预算编制说明",
        "本年公务接待费预算50万元，比上年预算增加50万元，增长100%。",
    )
    issues, outcomes = run_rules_with_outcomes(doc, False, report_kind="budget")
    assert RULE_ID in {outcome.rule_id for outcome in outcomes}, (
        "budget 规则集未执行 CMM-007——通用规则接线断裂"
    )
    hits = [i for i in issues if i.rule == RULE_ID and (i.location or {}).get("zero_base")]
    assert len(hits) == 1
    location = hits[0].location
    assert location["current_amount"] == "50"
    assert location["delta_amount"] == "50"
    assert location["derived_prior"] == "0.00"
    assert location["declared_percent"] == "100"
    assert hits[0].severity == "error"


# ---------------------------------------------------------------------------
# 合同负例 A~J（合成 contract fixture，非真实样张）
# ---------------------------------------------------------------------------


def test_case_a_only_delta_without_percent_is_legal():
    """反例 A：只有增减额、没有百分比（上年0→本年0.30 合法）→ 0 finding。"""
    doc = _contract_doc(
        _final_page(
            "公务接待费支出决算为0.30万元，完成预算的37.5%。",
            "公务接待费支出决算增加0.30万元。",
        )
    )
    assert _run(doc) == []


def test_case_b_from_zero_wording_is_legal():
    """反例 B：「由0增加至0.30万元」没有有限百分比 → 0 finding。"""
    doc = _contract_doc(
        _final_page("公务接待费支出决算由0万元增加至0.30万元。")
    )
    assert _run(doc) == []


def test_case_c_normal_increase_recompute():
    """反例 C：本期120/增加20/增长20% → prior=100，复算一致 → 0 finding。"""
    doc = _contract_doc(
        _final_page(
            "公务接待费支出决算为120万元。",
            "公务接待费支出决算增加20万元，增长20%。",
        )
    )
    assert _run(doc) == []


def test_case_d_wrong_percent_is_a_finding():
    """反例 D：本期120/增加20/却写增长50% → 复算 20% ≠ 50% → finding（>1pp → error）。"""
    doc = _contract_doc(
        _final_page(
            "公务接待费支出决算为120万元。",
            "公务接待费支出决算增加20万元，增长50%。",
        )
    )
    issues = _run(doc)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.severity == "error"
    assert issue.location["recomputed_percent"] == "20.00"
    assert issue.location["declared_percent"] == "50"
    assert not issue.location["zero_base"]


def test_case_e_direction_conflict_is_a_finding():
    """反例 E：本期120/增加20/却写下降20% → 方向矛盾 → finding。"""
    doc = _contract_doc(
        _final_page(
            "公务接待费支出决算为120万元。",
            "公务接待费支出决算增加20万元，下降20%。",
        )
    )
    issues = _run(doc)
    assert len(issues) == 1
    assert issues[0].location["direction_conflict"] is True


def test_case_f_normal_decrease_recompute():
    """反例 F：本期80/减少20/下降20% → prior=100，复算一致 → 0 finding。"""
    doc = _contract_doc(
        _final_page(
            "公务接待费支出决算为80万元。",
            "公务接待费支出决算减少20万元，下降20%。",
        )
    )
    assert _run(doc) == []


def test_case_g_completion_rate_is_not_yoy():
    """反例 G：「完成预算80%」是完成率不是同比 → CMM-007 不处理。"""
    doc = _contract_doc(
        _final_page(
            "公务接待费预算100万元，决算80万元，完成预算的80%。",
        )
    )
    assert _run(doc) == []


def test_case_h_share_percent_is_not_yoy():
    """反例 H：「占三公经费20%」是占比不是同比 → CMM-007 不处理。"""
    doc = _contract_doc(_final_page("公务接待费占三公经费总额的20%。"))
    assert _run(doc) == []


def test_case_i_no_cross_indicator_splicing():
    """反例 I：接待费本期、车辆费增减额、接待费百分比——三句分属不同指标/槽位，
    绝不能拼接成一个语义单元。"""
    doc = _contract_doc(
        _final_page(
            "公务接待费支出决算为0.30万元。",
            "公务用车运行维护费支出决算增加0.30万元。",
            "公务接待费支出决算增长100%。",
        )
    )
    assert _run(doc) == []


def test_hundred_percent_with_nonzero_base_is_legal():
    """任务 §三十九的反例（Mutation B 守卫）：本期200/增加100/增长100% ——
    复算 prior=100，增长 100% 完全合法，绝不能只凭「增长100%」关键词报零基数。"""
    doc = _contract_doc(
        _final_page(
            "公务接待费支出决算为200万元。",
            "公务接待费支出决算增加100万元，增长100%。",
        )
    )
    assert _run(doc) == []


def test_case_j_multiple_current_candidates_is_ambiguity():
    """反例 J：同节两处本期披露不一致（120 vs 130）→ parse_ambiguity，
    禁止取最近/取首个。"""
    doc = _contract_doc(
        _final_page(
            "公务接待费支出决算为120万元。",
            "公务接待费支出决算为130万元。",
            "公务接待费支出决算增加20万元，增长20%。",
        )
    )
    with pytest.raises(RuleDeferred) as excinfo:
        _run(doc)
    assert excinfo.value.status == "insufficient_data"
    detail = f"{excinfo.value.detail} {' '.join(excinfo.value.unresolved_reasons or [])}"
    assert "parse_ambiguity" in detail and "本期金额" in detail
    assert excinfo.value.partial_issues == []


# ---------------------------------------------------------------------------
# 显式基期四元交叉验证（任务 §二十八）
# ---------------------------------------------------------------------------


def test_explicit_prior_consistent_quadruple():
    """上年100/本年120/增加20/增长20%：显式基期=推导基期=100，复算一致 → 0。"""
    doc = _contract_doc(
        _final_page(
            "上年公务接待费支出决算为100万元。",
            "本年公务接待费支出决算为120万元，比上年增加20万元，增长20%。",
        )
    )
    assert _run(doc) == []


def test_explicit_prior_conflicts_with_derived_prior():
    """上年90/本年120/增加20 → 推导基期100 ≠ 显式90：四元不能同时成立 → finding。"""
    doc = _contract_doc(
        _final_page(
            "上年公务接待费支出决算为90万元。",
            "本年公务接待费支出决算为120万元，比上年增加20万元，增长20%。",
        )
    )
    issues = _run(doc)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.severity == "error"
    assert issue.location["explicit_prior"] == "90"
    assert issue.location["derived_prior"] == "100.00"
    assert "不能同时成立" in issue.message


# ---------------------------------------------------------------------------
# 台账收口（Mutation C 的固化守卫）
# ---------------------------------------------------------------------------


def _profile_receipt(rule_status: str) -> Dict[str, Any]:
    return {"rule_statuses": {RULE_ID: rule_status}}


def test_obligation_gap_is_closed_and_receipt_drives_status():
    """缺口收口：budget/final 两个台账都不再 not_implemented；回执驱动完成。"""
    assert RULE_ID in registered_rule_ids("final")
    assert RULE_ID in registered_rule_ids("budget")

    from src.engine.check_obligations import (
        OBLIGATION_COMPLETED,
        OBLIGATION_NOT_IMPLEMENTED,
    )

    def _instance(ledger: Dict[str, Any]) -> Dict[str, Any]:
        items = [
            item for item in ledger["instances"] if item["obligation_id"] == OBLIGATION_ID
        ]
        assert items, "台账缺少 OBL-TREND-ZERO-BASE 实例"
        return items[0]

    for kind in ("final", "budget"):
        ledger = build_obligation_ledger(None, report_kind=kind)
        instance = _instance(ledger)
        assert instance["status"] != OBLIGATION_NOT_IMPLEMENTED
        assert instance["missing_checkers"] == []

    completed = build_obligation_ledger(
        None, report_kind="final", rule_execution_summary=_profile_receipt("fail")
    )
    receipt = _instance(completed)
    assert receipt["status"] == OBLIGATION_COMPLETED
    assert f"{RULE_ID}=fail" in receipt["detail"]

    insufficient = build_obligation_ledger(
        None, report_kind="final", rule_execution_summary=_profile_receipt("insufficient_data")
    )
    deferred = _instance(insufficient)
    assert deferred["status"] != OBLIGATION_COMPLETED, (
        "checker 取数不足时义务不得自动完成（继续阻塞人工补核）"
    )


def test_withdrawing_the_checker_puts_the_obligation_back_as_a_gap(monkeypatch):
    """Mutation C 自动化：从 ALL_COMMON_RULES 撤下 → budget/final 两个台账都回
    not_implemented——否则"缺口数下降"可能来自清单改动而非真实能力。"""
    from src.engine import common_rules

    monkeypatch.setattr(
        common_rules,
        "ALL_COMMON_RULES",
        [
            rule
            for rule in common_rules.ALL_COMMON_RULES
            if getattr(rule, "code", "") != RULE_ID
        ],
    )
    assert RULE_ID not in registered_rule_ids("final")
    assert RULE_ID not in registered_rule_ids("budget")

    for kind in ("final", "budget"):
        ledger = build_obligation_ledger(None, report_kind=kind)
        instance = next(
            item for item in ledger["instances"] if item["obligation_id"] == OBLIGATION_ID
        )
        assert instance["status"] == OBLIGATION_NOT_IMPLEMENTED
        assert instance["missing_checkers"] == [RULE_ID]
