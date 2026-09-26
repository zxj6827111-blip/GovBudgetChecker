"""V33-DISCLOSURE-PERCENT-UNIT：百分比写法完整性（不得漏百分号）。

真值来源（冻结，不得改来迁就实现）
--------------------------------
**Truth F01（REAL，宜川）**：宜川路街道 2025 年度决算（41 页，SHA
``f809eef2…``）P28「（二）一般公共预算财政拨款支出决算结构情况」：

    城乡社区支出(类)15411.43 万元，占 76.23;住房保障支出(类)1076.78 万元，占 5.33%。

同枚举句 9 项结构占比中其余 8 项全部带 %，唯独此项缺百分号；复算
15411.43/20218.21×100 = 76.2255 → 76.23，与文中分母精确吻合。人工判定
（outputs/sample_validation_20260916/adjudication.json Y08）：severity「低」、
title「结构占比缺百分号」、system_match=miss（当时系统未报告）。

夹具口径（诚实声明）：真值 F01 是 **REAL**（宜川冻结夹具，SHA 双锁，
与 WP4-A/B/C/D/E 共用）；其余正例/负例 A~J 与边界用例均为
**CONTRACT FIXTURE**（合成，非真实样张）。全真实样张（uploads/
putuo_final_samples 7 份 + samples/good + samples/bad）扫描结果：
仅宜川 1 条真命中、0 误报（2026-09-26 WP4-F 现场复核）。
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
from src.engine.common_rules import R33DisclosurePercentUnit  # noqa: E402

RULE_ID = "V33-DISCLOSURE-PERCENT-UNIT"
OBLIGATION_ID = "OBL-DISCLOSURE-PERCENT-UNIT"

#: 宜川路街道 2025 年度决算（Truth F01；与 WP4-A/B/C/D/E 共用冻结夹具，SHA 双锁）
YICHUAN_FIXTURE = ROOT / "tests" / "fixtures" / "cross_san_gong_truth_page_data.json"
YICHUAN_SOURCE_SHA = "f809eef2afa91d258d5fbe4b0d1553dbe3a8ef9c4688d07e3a73aeaecd02ec03"
YICHUAN_FIXTURE_SHA = "45d761f3e3b7dc2ac023f1224bf4f02f7496860cca33b9f925ac8da991dcab4c"

#: 真值页原文（夹具 P28 逐字摘录，软换行保留）——独立于夹具内容变动，双重锁定
TRUTH_PAGE_TEXT = (
    "财政拨款支出决算结构情况\n"
    "一般公共预算财政拨款支出 20218.21 万元，主要用于以下\n"
    "方面：一般公共服务支出（类）207.40 万元，占 1.03%；公共安\n"
    "全支出（类）556.78 万元，占 2.75%;教育支出（类）26.02 万\n"
    "元，占 0.13%；科学技术支出(类)10.00 万元，占 0.05%;文化旅\n"
    "游体育与传媒支出(类)86.55 万元，占 0.43%;社会保障和就业支\n"
    "出(类)2452.66 万元，占 12.13%;卫生健康支出(类)265.57 万元，\n"
    "占 1.31%;节能环保支出(类)125.00 万元，占 0.62%;城乡社区支\n"
    "出(类)15411.43 万元，占 76.23;住房保障支出(类)1076.78 万\n"
    "元，占 5.33%。\n"
    "（三）一般公共预算财政拨款支出决算具体情况"
)


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


def _document(payload: Dict[str, Any]) -> Any:
    return build_document(
        path=f"{payload.get('doc_id', 'sample')}.pdf",
        page_texts=list(payload["page_texts"]),
        page_tables=json.loads(json.dumps(payload.get("page_tables", []))),
        filesize=0,
    )


def _run(*page_texts: str) -> List[Any]:
    """合成 contract 文档：页 1 提供年度锚，其后每参量一页。"""
    doc = build_document(
        path="contract-percent-unit.pdf",
        page_texts=["某某局 2025 年度部门决算公开", *page_texts],
        page_tables=[],
        filesize=0,
    )
    return list(R33DisclosurePercentUnit().apply(doc))


def _rule_hits(issues: List[Any]) -> List[Any]:
    return [i for i in issues if i.rule == RULE_ID]


# ---------------------------------------------------------------------------
# 真实 truth 正例（REAL）
# ---------------------------------------------------------------------------


def test_truth_f01_yichuan_reports_exactly_one_missing_unit(yichuan):
    """Truth F01：宜川 P28「占 76.23」缺百分号 → 全文档恰好 1 条 finding。

    同枚举句其余 8 项全部带 %、P28 前后各页无数值形态歧义——本测试同时
    锁死"不误报"（全文档 41 页仅此 1 条）。
    """
    issues = _rule_hits(_run_on_yichuan(yichuan))

    assert len(issues) == 1, [getattr(i, "message", "") for i in issues]
    issue = issues[0]
    assert issue.rule == RULE_ID
    assert issue.severity == "info", "人工判定 severity=低（格式/表达规范类），对应引擎 info"

    location = issue.location
    assert location["page"] == 28
    assert location["section"] == "（二）一般公共预算财政拨款支出决算结构情况"
    assert location["predicate"] == "占"
    assert location["numeric_text"] == "76.23"
    assert location["normalized_value"] == "76.23"
    assert location["unit_present"] is False
    assert location["expected_unit"] == "percent"
    assert location["obligation_id"] == OBLIGATION_ID
    assert location["fiscal_year"] == 2025
    assert "76.23" in issue.message
    assert "76.23%" not in issue.message, "文案不得断言正确值是 76.23%（口径未证实）"
    assert "百分比单位缺失" in issue.message
    assert "76.23" in issue.evidence_text


def _run_on_yichuan(yichuan: Dict[str, Any]) -> List[Any]:
    return list(R33DisclosurePercentUnit().apply(_document(yichuan)))


def test_truth_f01_frozen_page_text_reproduces_the_hit():
    """真值页逐字冻结文本必须复现命中（与夹具解耦的双保险）。"""
    issues = _run("某某街道 2025 年度部门决算", TRUTH_PAGE_TEXT)
    assert len(issues) == 1
    assert issues[0].location["numeric_text"] == "76.23"


# ---------------------------------------------------------------------------
# 双注册与真实管线执行（budget + final）
# ---------------------------------------------------------------------------


def test_rule_in_both_registries():
    """任务 §二十九/三十六：通用规则进 common registry，budget 与 final
    两个注册表都必须包含（不允许复制两份 checker）。"""
    assert RULE_ID in registered_rule_ids("final")
    assert RULE_ID in registered_rule_ids("budget")


def test_truth_pipeline_executes_in_full_final_run(yichuan):
    """真实 truth pipeline：完整 final 规则集执行必须产出本规则 fail 结论。"""
    issues, outcomes = run_rules_with_outcomes(_document(yichuan), False, report_kind="final")

    rule_codes = {outcome.rule_id for outcome in outcomes}
    assert RULE_ID in rule_codes, "final 规则集未执行本规则——注册表与执行集脱节"
    outcome = next(item for item in outcomes if item.rule_id == RULE_ID)
    assert outcome.status == STATUS_FAIL
    assert any(
        issue.rule == RULE_ID and "百分比单位缺失" in issue.message for issue in issues
    ), "完整 final 执行未保留缺百分号 finding（fail 的 finding 不得被丢弃）"


def test_contract_pipeline_executes_in_full_budget_run():
    """budget 管线必须真实执行本规则（不能仅 unit test 实例化类）。"""
    doc = build_document(
        path="contract-budget-percent.pdf",
        page_texts=[
            "某某局 2025 年度部门预算公开",
            "二、部门预算总体说明\n政府采购货物类支出占比76.23，工程类支出占20.00%。",
        ],
        page_tables=[],
        filesize=0,
    )
    issues, outcomes = run_rules_with_outcomes(doc, False, report_kind="budget")

    assert RULE_ID in {outcome.rule_id for outcome in outcomes}, (
        "budget 规则集未执行本规则——通用规则接线断裂"
    )
    hits = [i for i in issues if i.rule == RULE_ID]
    assert len(hits) == 1
    assert hits[0].location["predicate"] == "占比"
    assert hits[0].location["numeric_text"] == "76.23"


# ---------------------------------------------------------------------------
# 合同正例（缺单位 → finding）
# ---------------------------------------------------------------------------


def test_contract_case_g_zhanbi_without_unit_is_a_finding():
    """Case G：占比76.23（无数值后缀）→ finding（占比是强百分比谓词）。"""
    issues = _run("货物类支出占比76.23，工程类支出情况另述。")
    assert len(issues) == 1
    assert issues[0].location["predicate"] == "占比"
    assert issues[0].location["numeric_text"] == "76.23"


def test_contract_case_h_bizhong_without_unit_is_a_finding():
    """Case H：比重为76.23 → finding（比重为强百分比谓词）。"""
    issues = _run("货物类支出比重为76.23，工程类支出情况另述。")
    assert len(issues) == 1
    assert issues[0].location["predicate"] == "比重"


def test_contract_case_i_bili_without_unit_is_a_finding():
    """Case I：比例为76.23 → finding（比例为强百分比谓词）。"""
    issues = _run("货物类支出比例为76.23，工程类支出情况另述。")
    assert len(issues) == 1
    assert issues[0].location["predicate"] == "比例"


def test_contract_task_fifteen_mixed_sentence_reports_first_only():
    """任务 §十五合同正例：货物类支出占76.23，工程类支出占20.00%。→ 仅第一处。"""
    issues = _run("政府采购支出中货物类支出占76.23，工程类支出占20.00%。")
    assert len(issues) == 1
    assert issues[0].location["numeric_text"] == "76.23"
    assert "76.23" in issues[0].evidence_text
    assert "20.00" not in issues[0].location["numeric_text"]


def test_contract_multiple_percentages_reports_only_the_bare_one():
    """任务 §三十三：三项枚举只有第一处缺单位 → 仅第一处 finding。"""
    issues = _run("货物支出占76.23，工程支出占20.00%，服务支出占3.77％。")
    assert len(issues) == 1
    assert issues[0].location["numeric_text"] == "76.23"
    span = issues[0].evidence_text
    assert "76.23" in span


def test_contract_task_nineteen_over_hundred_is_still_a_percentage():
    """任务 §十九：value > 100 不能自动排除百分比语义（占比可超 100）。"""
    issues = _run("全年执行进度占比达到120.5，超出原因另述。")
    assert len(issues) == 1
    assert issues[0].location["numeric_text"] == "120.5"


def test_contract_task_twenty_decimal_ratio_reports_ambiguous_wording():
    """任务 §二十：占比为0.7623 → finding 但文案只能说「口径不明确」，
    不得断言正确值是 76.23%。"""
    issues = _run("货物类支出占比为0.7623，工程类支出情况另述。")
    assert len(issues) == 1
    assert "口径不明确" in issues[0].message
    assert "76.23%" not in issues[0].message
    assert "76.23" not in issues[0].message


# ---------------------------------------------------------------------------
# 合同负例（任务 §三十一 Case A~J + §三十二 多数字隔离）
# ---------------------------------------------------------------------------


def test_case_a_ascii_percent_is_legal():
    """Case A：占76.23% → 0 finding。"""
    assert _run("货物类支出占76.23%。") == []


def test_case_b_fullwidth_percent_is_legal():
    """Case B：占76.23％（全角）→ 0 finding。"""
    assert _run("货物类支出占76.23％；其他支出另述。") == []


def test_case_c_chinese_percent_wording_is_legal():
    """Case C：占百分之七十六点二三 → 0 finding（中文百分数已完整表达）。"""
    assert _run("货物类支出占百分之七十六点二三。") == []


def test_case_d_land_area_is_not_a_percentage():
    """Case D：占地76.23平方米 → 0 finding（「占地」是面积语义）。"""
    assert _run("项目占地76.23平方米。") == []
    assert _run("建筑占地76.23亩。") == []
    assert _run("占地面积76.23平方米，绿化面积另述。") == []


def test_case_e_fund_occupation_is_not_a_percentage():
    """Case E：占用资金76.23万元 → 0 finding（「占用」是金额语义）。"""
    assert _run("占用资金76.23万元。") == []
    assert _run("占用预算76.23万元，资金来源另述。") == []


def test_case_f_percentage_points_are_legal():
    """Case F：增加2个百分点 → 0 finding（百分点不是百分比，不缺单位）。"""
    assert _run("占比增加2个百分点。") == []
    assert _run("下降1.5个百分点。") == []
    assert _run("增加0.3个百分点。") == []


def test_case_j_amount_unit_is_not_a_percentage():
    """Case J：占76.23万元 → 0 finding（金额单位明确）。"""
    assert _run("货物支出占76.23万元。") == []


def test_quantity_units_are_not_percentages():
    """任务 §十：存在明确数量单位 → 0 finding（平方米/人/个/项/天/亿元…）。"""
    for text in (
        "占76.23平方米。",
        "占76.23人。",
        "占76.23人次。",
        "占76.23个。",
        "占76.23项。",
        "占76.23天。",
        "占76.23元。",
        "占76.23亿元。",
    ):
        issues = _run(text)
        assert issues == [], f"{text} 不应报错"


def test_task_32_multiple_numbers_are_isolated():
    """任务 §三十二：金额与占比同句 → 0 finding（76.23 不得误绑占比）。"""
    assert _run("项目支出76.23万元，占一般公共预算财政拨款支出的25.00%。") == []


def test_task_21_cross_clause_numbers_are_not_bound():
    """任务 §二十一：「占」的宾语是普通名词时，后续句数字不得绑定到「占」。"""
    assert _run("该项目占主要部分。2025年支出76.23万元。") == []


def test_task_12_34_chinese_percent_wordings_are_all_legal():
    """任务 §三十四：中文百分数不得要求再补 % 字符。"""
    for text in ("占比为百分之七十六点二三。", "占比为百分之百。", "占比为百分之零点五。"):
        assert _run(text) == [], f"{text} 不应报错"


def test_task_13_percentage_points_never_reported():
    """任务 §十三：「百分点」是完整单位，且不得与百分比混为同一数学量。"""
    assert _run("提高2个百分点。") == []
    assert _run("占比提高2.5个百分点。") == []


# ---------------------------------------------------------------------------
# 边界纪律
# ---------------------------------------------------------------------------


def test_v33_232_boundary_precision_is_out_of_scope():
    """任务 §二十四/二十五：占76.234% 有单位 → 本规则 0 finding。
    小数位精度问题归 V33-232，两条规则各管各的。"""
    assert _run("货物类支出占76.234%。") == []


def test_task_23_cross_page_binding_is_fail_closed():
    """任务 §二十三：「占」在 P1 尾、数字在 P2 首 → 不得绑定（0 finding）。"""
    issues = _run("城乡社区支出占", "76.23;住房保障支出占5.33%。")
    assert issues == []


def test_digit_run_dislocation_is_fail_closed():
    """财政局 2024 P21 实测形态：数字与文字分离提取造成「占⏎60.99 2.07%…」
    伪绑定 → fail-closed 0 finding。"""
    dislocated = (
        "（一）一般公共预算财政拨款支出决算总体情况一般公共预算财政拨款支出 万元，占本年支出合计的 。与 年\n"
        "2940.05（类） 万元，占 ；社会保障和就业支出（类） 万元，占 ；\n"
        "2106.66 71.73% 326.72 11.11%卫生健康支出（类） 万元，占 ；住房保障支出（类） 万元，占\n"
        "60.99 2.07% 445.69；\n"
        "15.16%"
    )
    assert _run(dislocated) == []


def test_soft_wrapped_predicate_and_number_are_bound():
    """任务 §二十二：软换行「占␊80」经项目现有 merge 纪律拼接后必须命中；
    禁止的是删除全文空白后跨段全局拼接，不是拒绝行内软换行。"""
    issues = _run("货物类支出占\n80，其他支出另述。")
    assert len(issues) == 1
    assert issues[0].location["numeric_text"] == "80"


def test_soft_wrapped_decimal_number_at_next_line_start_is_bound():
    """任务 §二十二：行首「76.23」被项目现有 merge 纪律（_NEW_PARAGRAPH_RE
    的 \d+\.\d+ 条目形态）判为新段落起点时，行尾谓词 + 次行行首数值的
    跨行绑定补上该形态——仍走同一后缀分类，与主扫描按页+谓词+数值去重。"""
    issues = _run("货物类支出占\n76.23，其他支出另述。")
    assert len(issues) == 1
    assert issues[0].location["numeric_text"] == "76.23"
    assert issues[0].location["line_break"] is True


def test_line_end_predicate_before_plain_number_row_is_fail_closed():
    """行尾谓词 + 次行纯数值行（表格线性化：表头「占比」独立行 + 数据行）
    → fail-closed 0 finding，不把表头当占比谓词。"""
    issues = _run("项目 数量 占比\n76.23\n住房保障支出（类）1076.78 万元")
    assert issues == []


# ---------------------------------------------------------------------------
# R2 P1 整改：occurrence 级 dedup（同页同谓词同数值的不同 occurrence 各自成
# finding，只有同一物理 occurrence 的主扫描/跨行双命中才合并）
# ---------------------------------------------------------------------------


def test_r2_same_page_same_value_two_occurrences_both_report():
    """任务 §七（重复值测试 A）：同页两处「占76.23」→ 2 findings，
    source location 与 evidence span 均不同，不得被粗去重吞掉第二条。"""
    issues = _run(
        "货物类支出占76.23，相关情况另述。\n服务类支出占76.23，相关情况另述。"
    )
    assert len(issues) == 2, [getattr(i, "message", "") for i in issues]
    first, second = issues
    for issue in issues:
        assert issue.location["numeric_text"] == "76.23"
        assert issue.location["predicate"] == "占"
    assert first.location["source_start"] != second.location["source_start"]
    assert first.location["source_end"] != second.location["source_end"]
    assert first.evidence_text != second.evidence_text


def test_r2_same_page_same_value_different_sections_both_report():
    """任务 §八（重复值测试 B）：同页不同章节的两处「占76.23」→ 2 findings，
    section 各自归属，不得合并。"""
    page = (
        "一、政府采购情况说明\n货物支出占76.23，相关情况另述。\n"
        "二、资产情况说明\n固定资产占76.23，相关情况另述。"
    )
    issues = _run(page)
    assert len(issues) == 2, [getattr(i, "message", "") for i in issues]
    sections = {issue.location["section"] for issue in issues}
    assert sections == {"一、政府采购情况说明", "二、资产情况说明"}
    starts = [issue.location["source_start"] for issue in issues]
    assert starts == sorted(starts)


def test_r2_same_page_same_composite_predicate_two_occurrences():
    """任务 §九（重复值测试 C）：同页两处「占比76.23」→ 2 findings，
    复合谓词同样不被粗去重。"""
    issues = _run("货物支出占比76.23。\n服务支出占比76.23。")
    assert len(issues) == 2
    assert {issue.location["predicate"] for issue in issues} == {"占比"}
    assert (
        issue.location["source_start"] for issue in issues
    )


def test_r2_main_scan_and_line_break_same_occurrence_stay_merged():
    """任务 §六：主扫描与跨行补绑定命中同一物理 occurrence 时仍只 1 finding
    （单通道后由匹配唯一性结构性保证，测试锁死防回归）。"""
    issues = _run("货物类支出占\n76.23，其他支出另述。")
    assert len(issues) == 1
    assert issues[0].location["line_break"] is True
    assert issues[0].location["source_end"] > issues[0].location["source_start"]


# ---------------------------------------------------------------------------
# R2 P1 整改：line-break finding 的 section 必须 occurrence-local
# ---------------------------------------------------------------------------


def test_r2_section_a_line_break_section_is_occurrence_local():
    """任务 §十四（section test A）：line-break 命中在（一）内、页尾还有
    （二）→ section 必须是（一），绝不能取整页最后一个章节。"""
    page = (
        "（一）政府采购情况说明\n货物支出占\n76.23，相关情况另述。\n"
        "（二）资产情况说明\n固定资产管理情况正常。"
    )
    issues = _run(page)
    assert len(issues) == 1
    assert issues[0].location["section"] == "（一）政府采购情况说明"
    assert issues[0].location["line_break"] is True


def test_r2_section_b_two_line_break_occurrences_keep_own_sections():
    """任务 §十五（section test B）：同页两个 line-break occurrence 分属
    （一）（二）→ 2 findings，各自 section 不串。"""
    page = (
        "（一）政府采购情况说明\n货物支出占\n76.23，相关情况另述。\n"
        "（二）资产情况说明\n固定资产占\n55.50，相关情况另述。"
    )
    issues = _run(page)
    assert len(issues) == 2, [getattr(i, "message", "") for i in issues]
    by_value = {issue.location["numeric_text"]: issue for issue in issues}
    assert by_value["76.23"].location["section"] == "（一）政府采购情况说明"
    assert by_value["55.50"].location["section"] == "（二）资产情况说明"


def test_r2_section_c_no_heading_before_candidate_stays_none():
    """任务 §十六（section test C）：candidate 之前无章节标题 → section=None，
    不得借用 candidate 之后的（一）章节。"""
    page = "货物支出占\n76.23，情况另述。\n（一）后续章节说明\n其它内容正常。"
    issues = _run(page)
    assert len(issues) == 1
    assert issues[0].location["section"] is None


def test_r2_same_line_section_is_also_occurrence_local():
    """同行命中的 section 同样只向前取（candidate 前的标题），页尾章节不回溯。"""
    page = "（一）政府采购情况说明\n货物支出占76.23，情况另述。\n（二）资产情况说明"
    issues = _run(page)
    assert len(issues) == 1
    assert issues[0].location["section"] == "（一）政府采购情况说明"


def test_no_text_defers_instead_of_passing():
    """全文档无文本 → RuleDeferred（insufficient_data），不得静默通过。"""
    doc = build_document(path="empty.pdf", page_texts=["", ""], page_tables=[], filesize=0)
    with pytest.raises(RuleDeferred):
        R33DisclosurePercentUnit().apply(doc)


# ---------------------------------------------------------------------------
# 台账收口（Mutation E 的固化守卫）
# ---------------------------------------------------------------------------


def _obligation_instance(ledger: Dict[str, Any]) -> Dict[str, Any]:
    items = [
        item for item in ledger["instances"] if item["obligation_id"] == OBLIGATION_ID
    ]
    assert items, "台账缺少 OBL-DISCLOSURE-PERCENT-UNIT 实例"
    return items[0]


def test_obligation_gap_is_closed_and_receipt_drives_status():
    """缺口收口：budget/final 两个台账都不再 not_implemented；回执驱动完成。"""
    for kind in ("final", "budget"):
        ledger = build_obligation_ledger(None, report_kind=kind)
        instance = _obligation_instance(ledger)
        assert instance["status"] != OBLIGATION_NOT_IMPLEMENTED
        assert instance["missing_checkers"] == []

    completed = build_obligation_ledger(
        None, report_kind="final", rule_execution_summary={"rule_statuses": {RULE_ID: "fail"}}
    )
    receipt = _obligation_instance(completed)
    assert receipt["status"] == "completed"
    assert f"{RULE_ID}=fail" in receipt["detail"]

    insufficient = build_obligation_ledger(
        None,
        report_kind="final",
        rule_execution_summary={"rule_statuses": {RULE_ID: "insufficient_data"}},
    )
    deferred = _obligation_instance(insufficient)
    assert deferred["status"] != "completed", (
        "checker 取数不足时义务不得自动完成（继续阻塞人工补核）"
    )


def test_withdrawing_the_checker_puts_the_obligation_back_as_a_gap(monkeypatch):
    """Mutation E 自动化：从 ALL_COMMON_RULES 撤下 → budget/final 两个台账都
    回 not_implemented——否则"缺口数下降"可能来自清单改动而非真实能力。"""
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
