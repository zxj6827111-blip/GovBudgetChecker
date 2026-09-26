"""V33-SG-COMPLETION：三公经费说明四项细化披露完整性（final only）。

真值来源（冻结，不得改来迁就实现）
--------------------------------
**Truth SGC-01（REAL，官方人工检查表）**：宜川路街道 2025 年度决算（41 页，
SHA ``f809eef2…``）官方检查表《…宜川路街道办事处（部门决算）检查表.xlsx》
"是否合格=否"行——「是否细化'公务用车购置及运行费'：公开'公务用车购置费'、
'公务用车运行费'→ 否；问题1：第三部分缺少以下'三公'经费细化披露字段：
公务用车购置费。」对照样张 P36：说明（二）2 只写"公务用车购置及运行维护费
支出 19.56 万元。其中：公务用车运行维护支出 19.56 万元…"，购置费既无金额
也无未发生说明。2026-09-16 重跑（outputs/sample_validation_20260916，26 条
finding 全 info）未报告此问题（historical_system_result=miss）。

同批检查表另有「因公出国团组数」「公务用车购置数」两行"否"——数量要素的
本地披露标准未定（comparison.md 对同类问题判"待裁决"），本轮只收编四项
**金额**披露，不越界到数量要素（团组/批次/人次归 V33-246 领域）。

夹具口径（诚实声明）：真值 SGC-01 与负例回归（石泉/文旅）均为 **REAL**
（三份样张冻结夹具，SHA 双锁，分别与 WP4-A/B/C/D/E/F、WP4-C、WP4-D 共用）；
Case A~E 与边界用例为 **CONTRACT FIXTURE**（合成，非真实样张）。
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
    OBLIGATION_COMPLETED,
    OBLIGATION_NOT_IMPLEMENTED,
    build_obligation_ledger,
    registered_rule_ids,
)
from src.engine.pipeline import run_rules_with_outcomes  # noqa: E402
from src.engine.rule_outcome import STATUS_FAIL, RuleDeferred  # noqa: E402
from src.engine.rules_v33 import (  # noqa: E402
    R33SGCompletion,
    build_document,
)

RULE_ID = "V33-SG-COMPLETION"
OBLIGATION_ID = "OBL-SG-COMPLETION"

#: 宜川路街道 2025 年度决算（Truth SGC-01；与 WP4-A/B/C/D/E/F 共用冻结夹具）
YICHUAN_FIXTURE = ROOT / "tests" / "fixtures" / "cross_san_gong_truth_page_data.json"
YICHUAN_SOURCE_SHA = "f809eef2afa91d258d5fbe4b0d1553dbe3a8ef9c4688d07e3a73aeaecd02ec03"
YICHUAN_FIXTURE_SHA = "45d761f3e3b7dc2ac023f1224bf4f02f7496860cca33b9f925ac8da991dcab4c"

#: 石泉路街道 2025 年度决算（REAL 负例；与 WP4-C 共用冻结夹具，SHA 双锁）
SHIQUAN_FIXTURE = ROOT / "tests" / "fixtures" / "shiquan_narrative_truth_page_data.json"
SHIQUAN_SOURCE_SHA = "e8315830f800e58038b19bfd0f1e36f5930d9da72a22c3e79f781036bd5289a4"
SHIQUAN_FIXTURE_SHA = "be8abd4685bc0de3fc2b87b91e0f3d32e79a50e369c9e4dc7aa3cfe5c28272d7"

#: 文化和旅游局 2025 年度部门决算（REAL 负例；与 WP4-D 共用冻结夹具，SHA 双锁）
WENLV_FIXTURE = ROOT / "tests" / "fixtures" / "wenlv_narrative_truth_page_data.json"
WENLV_SOURCE_SHA = "74e6afd06669098f57391a13dad63a6f2a15cac620ac9f32018e79649fe6a367"
WENLV_FIXTURE_SHA = "07813ae61b04cf9f3afd2bf93c69b357ff9aeefee3ba6a893b34d77d956e8252"


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
def shiquan() -> Dict[str, Any]:
    return _load_fixture(SHIQUAN_FIXTURE, SHIQUAN_SOURCE_SHA, SHIQUAN_FIXTURE_SHA)


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


def _run(*page_texts: str, page_tables: List[Any] | None = None) -> List[Any]:
    """合成 contract 文档：页 1 提供文种锚，其后每参量一页。"""
    doc = build_document(
        path="contract-sg-completion.pdf",
        page_texts=["某某局 2025 年度部门决算公开", *page_texts],
        page_tables=json.loads(json.dumps(page_tables or [])),
        filesize=0,
    )
    return list(R33SGCompletion().apply(doc))


def _hits(issues: List[Any], item_key: str | None = None) -> List[Any]:
    return [
        issue
        for issue in issues
        if issue.rule == RULE_ID and (item_key is None or issue.location.get("item_key") == item_key)
    ]


#: 三公说明章节骨架（contract 用例共用；编号主标题供章节定位）
SG_SECTION_TITLE = "七、财政拨款“三公”经费支出决算情况说明"
SG_SECTION_NEXT = "八、政府性基金预算财政拨款收入支出决算情况说明\n政府性基金预算财政拨款收入 100 万元。"


def _sg_page(*paras: str) -> str:
    return SG_SECTION_TITLE + "\n" + "\n".join(paras) + "\n" + SG_SECTION_NEXT


# ---------------------------------------------------------------------------
# 真实 truth 正例（REAL：宜川，官方检查表"否"行）
# ---------------------------------------------------------------------------


def test_truth_sgc01_yichuan_reports_exactly_purchase_refinement(yichuan):
    """Truth SGC01：宜川三公说明（二）只披露合并金额与运行维护子项，购置费
    整段未披露 → 全文档恰好 1 条 finding，落在公务用车购置费。"""
    issues = _run_all_pages(yichuan)
    assert len(_hits(issues)) == 1, f"期望恰好 1 条，实得 {len(_hits(issues))}"
    issue = _hits(issues)[0]
    assert issue.location["three_public_item"] == "公务用车购置费"
    assert issue.location["item_key"] == "vehicle_purchase"
    assert issue.location["page"] == 36
    assert "三公" in (issue.location.get("section") or "")
    # 出国 0.00 / 运行 19.56 / 接待 0.3 都已披露，不得误报
    assert not _hits(issues, "overseas")
    assert not _hits(issues, "vehicle_operation")
    assert not _hits(issues, "reception")


def test_truth_sgc01_finding_carries_expected_actual_and_evidence(yichuan):
    """任务 §八：finding 必须可定位——带章节、期望披露、实际披露、证据段。"""
    issue = _hits(_run_all_pages(yichuan))[0]
    assert "公务用车购置费" in issue.message
    assert "期望" in issue.message and "实际" in issue.message
    assert issue.location["expected_disclosure"]
    assert "合并金额" in issue.location["actual_disclosure"]
    assert (issue.evidence_text or "").startswith("【章节:")
    assert "公务用车购置及运行维护费" in (issue.evidence_text or "")


def _run_all_pages(payload: Dict[str, Any]) -> List[Any]:
    return list(R33SGCompletion().apply(_document(payload)))


# ---------------------------------------------------------------------------
# 真实负例回归（REAL：石泉购置 25 细化、文旅购置 0 + 未新增公务用车）
# ---------------------------------------------------------------------------


def test_real_shiquan_with_itemized_purchase_passes(shiquan):
    """石泉（二）2 明示「公务用车购置支出为 25 万元」→ 0 finding。"""
    assert _run_all_pages(shiquan) == []


def test_real_wenlv_with_zero_purchase_and_not_occurred_passes(wenlv):
    """文旅（二）2「公务用车购置支出为 0 万元」+（一）「2025 年未新增公务用车」
    → 0 finding（合法缺项：0 披露与未发生说明都不算缺失）。"""
    assert _run_all_pages(wenlv) == []


# ---------------------------------------------------------------------------
# 合同用例 Case A~E（CONTRACT FIXTURE）
# ---------------------------------------------------------------------------


def test_contract_case_a_all_four_items_disclosed_passes():
    """Case A：四项完整（出国/购置/运行/接待 各带金额）→ 0。"""
    issues = _run(
        _sg_page(
            "（二）“三公”经费财政拨款支出决算具体情况说明。",
            "1、因公出国（境）费支出 1.20 万元。",
            "2、公务用车购置及运行维护费支出 30.00 万元。其中：",
            "公务用车购置支出为 10.00 万元。",
            "公务用车运行维护支出 20.00 万元。",
            "3、公务接待费支出 2.00 万元。",
        )
    )
    assert _hits(issues) == []


def test_contract_case_b_purchase_not_occurred_passes():
    """Case B：明示「本年度未发生公务用车购置」→ 0（未发生 ≠ 未披露）。"""
    issues = _run(
        _sg_page(
            "（二）“三公”经费财政拨款支出决算具体情况说明。",
            "1、因公出国（境）费支出 0.00 万元。",
            "2、公务用车购置及运行维护费支出 20.00 万元。其中：本年度未发生公务用车购置。",
            "公务用车运行维护支出 20.00 万元。",
            "3、公务接待费支出 0.50 万元。",
        )
    )
    assert _hits(issues) == []


def test_contract_case_c_reception_completely_missing_is_a_finding():
    """Case C：完全缺少公务接待费且无说明 → 1 条 finding（item=reception）。"""
    issues = _run(
        _sg_page(
            "（二）“三公”经费财政拨款支出决算具体情况说明。",
            "1、因公出国（境）费支出 0.00 万元。",
            "2、公务用车购置及运行维护费支出 20.00 万元。其中：",
            "公务用车购置支出为 0.00 万元。",
            "公务用车运行维护支出 20.00 万元。",
        )
    )
    assert len(_hits(issues, "reception")) == 1
    assert _hits(issues, "reception")[0].location["three_public_item"] == "公务接待费"
    assert not _hits(issues, "overseas")
    assert not _hits(issues, "vehicle_purchase")
    assert not _hits(issues, "vehicle_operation")


def test_contract_case_d_zero_amount_disclosed_passes():
    """Case D：金额为 0 但有披露（公务接待费 0 万元）→ 0。"""
    issues = _run(
        _sg_page(
            "（二）“三公”经费财政拨款支出决算具体情况说明。",
            "1、因公出国（境）费支出 0.00 万元。",
            "2、公务用车购置及运行维护费支出 20.00 万元。其中：",
            "公务用车购置支出为 0.00 万元。",
            "公务用车运行维护支出 20.00 万元。",
            "3、公务接待费支出 0 万元。",
        )
    )
    assert _hits(issues) == []


def test_contract_case_e_table_has_four_items_but_text_misses_one():
    """Case E：表七四项都有数值、说明缺购置细化 → finding。

    表格披露不参与本判定（表文一致性归 OBL-SG-TABLE-TEXT）——表里有
    购置费列数值也救不了说明侧的缺失。
    """
    table7 = [
        [
            ["财政拨款“三公”经费", "", "", "", "", "", "", "", "", "", "", ""],
            ["合计", "", "因公出国\n（境）费", "", "公务用车购置及运行维护费", "", "", "", "", "", "公务接待费", ""],
            ["", "", "", "", "小计", "", "公务用车\n购置费", "", "公务用车\n运行维护费", "", "", ""],
            ["预算数", "决算数", "预算数", "决算数", "预算数", "决算数", "预算数", "决算数", "预算数", "决算数", "预算数", "决算数"],
            ["31.47", "19.86", "0", "0", "30.67", "19.56", "0", "0", "30.67", "19.56", "0.80", "0.30"],
        ]
    ]
    issues = _run(
        _sg_page(
            "（二）“三公”经费财政拨款支出决算具体情况说明。",
            "1、因公出国（境）费支出 0.00 万元。",
            "2、公务用车购置及运行维护费支出 19.56 万元。其中：",
            "公务用车运行维护支出 19.56 万元。",
            "3、公务接待费支出 0.30 万元。",
        ),
        page_tables=[[], [], table7],
    )
    assert len(_hits(issues, "vehicle_purchase")) == 1
    assert not _hits(issues, "overseas")
    assert not _hits(issues, "vehicle_operation")
    assert not _hits(issues, "reception")


# ---------------------------------------------------------------------------
# 合法缺项与等效说明边界（未发生 vs 未披露）
# ---------------------------------------------------------------------------


def test_global_no_three_public_statement_covers_all_items():
    """整章「无三公经费」等效说明 → 四项全部视为已披露，0 finding。"""
    issues = _run(_sg_page("2025 年度本单位无三公经费财政拨款收支。"))
    assert _hits(issues) == []


def test_global_zero_total_covers_all_items():
    """三公合计披露为 0（四项必然为 0）→ 0 finding；合计非 0 不触发。"""
    zero = _run(
        _sg_page(
            "（一）“三公”经费财政拨款支出决算总体情况说明。",
            "“三公”经费财政拨款支出年初预算为 5.00 万元，支出决算为 0 万元。",
        )
    )
    assert _hits(zero) == []


def test_combined_disclosure_alone_does_not_satisfy_purchase_refinement():
    """合并披露「公务用车购置及运行维护费」合法于三项口径，但不满足购置
    细化——只有合并金额 + 运行子项、购置无金额无未发生 → finding（宜川形态
    的合同版；（一）出现合并主体同样不豁免）。"""
    issues = _run(
        _sg_page(
            "（一）“三公”经费财政拨款支出决算总体情况说明。",
            "“三公”经费财政拨款支出年初预算为 31.47 万元，支出决算为 19.86 万元，"
            "其中：因公出国（境）费决算为 0.00 万元；公务用车购置及运行维护费支出决算为 19.56 万元。",
            "（二）“三公”经费财政拨款支出决算具体情况说明。",
            "1、因公出国（境）费支出 0.00 万元。",
            "2、公务用车购置及运行维护费支出 19.56 万元。其中：",
            "公务用车运行维护支出 19.56 万元。",
            "3、公务接待费支出 0.30 万元。",
        )
    )
    assert len(_hits(issues, "vehicle_purchase")) == 1


def test_combined_only_without_any_subitem_reports_both_vehicle_items():
    """只有合并金额、购置与运行两个子项都没有 → 购置与运行各 1 条
    （检查表同一行要求同时公开两个细化字段，逐项可定位）。"""
    issues = _run(
        _sg_page(
            "（二）“三公”经费财政拨款支出决算具体情况说明。",
            "1、因公出国（境）费支出 0.00 万元。",
            "2、公务用车购置及运行维护费支出 19.56 万元。",
            "3、公务接待费支出 0.30 万元。",
        )
    )
    assert len(_hits(issues, "vehicle_purchase")) == 1
    assert len(_hits(issues, "vehicle_operation")) == 1


def test_yoy_paragraph_not_occurred_wording_discloses_purchase():
    """文旅形态：（一）同比段「2025 年未新增公务用车」与合并主体同分句，
    构成购置未发生披露 → 0 finding。"""
    issues = _run(
        _sg_page(
            "（一）“三公”经费财政拨款支出决算总体情况说明。",
            "公务用车购置及运行维护费支出减少的主要原因是 2025 年未新增公务用车。",
            "（二）“三公”经费财政拨款支出决算具体情况说明。",
            "1、因公出国（境）费支出 0.00 万元。",
            "2、公务用车购置及运行维护费支出 2.64 万元。其中：",
            "公务用车运行维护支出 2.64 万元。",
            "3、公务接待费支出 0.40 万元。",
        )
    )
    assert _hits(issues) == []


def test_missing_section_is_rule_deferred_not_finding():
    """三公说明章节不存在 → RuleDeferred（证据不足），不出确定性结论。"""
    doc = build_document(
        path="no-sg-section.pdf",
        page_texts=["某某局 2025 年度部门决算公开", "一、收入支出决算总体情况说明。"],
        page_tables=[],
        filesize=0,
    )
    with pytest.raises(RuleDeferred):
        R33SGCompletion().apply(doc)


def test_empty_page_texts_is_rule_deferred():
    doc = build_document(
        path="blank.pdf", page_texts=["", ""], page_tables=[], filesize=0
    )
    with pytest.raises(RuleDeferred):
        R33SGCompletion().apply(doc)


# ---------------------------------------------------------------------------
# final-only 注册、pipeline 执行与台账收口（Mutation C 的固化守卫）
# ---------------------------------------------------------------------------


def test_rule_registered_in_final_only():
    """final 必含；budget 不得含（OBL-SG-COMPLETION 是 final only）。"""
    assert RULE_ID in registered_rule_ids("final")
    assert RULE_ID not in registered_rule_ids("budget")


def test_truth_pipeline_executes_in_full_final_run(yichuan):
    """pipeline 面：final 规则集必须执行本规则且 STATUS_FAIL（宜川真值）。"""
    doc = _document(yichuan)
    issues, outcomes = run_rules_with_outcomes(doc, False, report_kind="final")
    rule_codes = {outcome.rule_id for outcome in outcomes}
    assert RULE_ID in rule_codes, "final 规则集未执行本规则——注册表与执行集脱节"
    outcome = next(item for item in outcomes if item.rule_id == RULE_ID)
    assert outcome.status == STATUS_FAIL
    hits = [issue for issue in issues if issue.rule == RULE_ID]
    assert len(hits) == 1, "完整 final 执行未保留购置费细化 finding（fail 的 finding 不得被丢弃）"


def test_obligation_gap_is_closed_and_receipt_drives_status():
    """缺口收口：OBL-SG-COMPLETION 不再 not_implemented；回执驱动完成状态。"""
    assert RULE_ID in registered_rule_ids("final")

    ledger = build_obligation_ledger(None, report_kind="final")
    instance = _obligation_instance(ledger)
    assert instance["status"] != OBLIGATION_NOT_IMPLEMENTED
    assert instance["missing_checkers"] == []

    completed = build_obligation_ledger(
        None,
        report_kind="final",
        rule_execution_summary={"rule_statuses": {RULE_ID: "fail"}},
    )
    receipt = _obligation_instance(completed)
    assert receipt["status"] == OBLIGATION_COMPLETED
    assert f"{RULE_ID}=fail" in receipt["detail"]

    insufficient = build_obligation_ledger(
        None,
        report_kind="final",
        rule_execution_summary={"rule_statuses": {RULE_ID: "insufficient_data"}},
    )
    deferred = _obligation_instance(insufficient)
    assert deferred["status"] != OBLIGATION_COMPLETED, (
        "checker 取数不足时义务不得自动完成（继续阻塞人工补核）"
    )


def test_final_ledger_still_expands_sg_completion_instance():
    """OBL-SG-COMPLETION 属三公六实例组：final 台账必须仍展开该实例。"""
    from src.engine.check_obligations import SAN_GONG_CHECK_OBLIGATION_IDS

    assert OBLIGATION_ID in SAN_GONG_CHECK_OBLIGATION_IDS
    ledger = build_obligation_ledger(None, report_kind="final")
    items = [
        item
        for item in ledger["instances"]
        if item["obligation_id"] == OBLIGATION_ID
    ]
    assert items, "final 台账缺少 OBL-SG-COMPLETION 实例"


def test_withdrawing_the_checker_puts_the_obligation_back_as_a_gap(monkeypatch):
    """Mutation C 自动化：从 ALL_RULES 撤下 → final 台账回到 not_implemented、
    registered_rule_ids 不含——否则"缺口数下降"可能来自清单改动而非真实能力。"""
    from src.engine import rules_v33

    monkeypatch.setattr(
        rules_v33,
        "ALL_RULES",
        [
            rule
            for rule in rules_v33.ALL_RULES
            if getattr(rule, "code", "") != RULE_ID
        ],
    )
    assert RULE_ID not in registered_rule_ids("final")
    instance = _obligation_instance(build_obligation_ledger(None, report_kind="final"))
    assert instance["status"] == OBLIGATION_NOT_IMPLEMENTED
    assert instance["missing_checkers"] == [RULE_ID]


def _obligation_instance(ledger: Dict[str, Any]) -> Dict[str, Any]:
    items = [
        item for item in ledger["instances"] if item["obligation_id"] == OBLIGATION_ID
    ]
    assert items, f"台账缺少 {OBLIGATION_ID} 实例"
    return items[0]
