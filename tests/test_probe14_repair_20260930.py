"""MR-P 批次（2026-09-30 市级探针裁决整改）验收测试。

事实源：docs/B1_PROBE14_FPFN_ADJUDICATION_20260930.md（commit 3062bcd）——
14 份上海市级决算 87 条高严重度 findings 人工裁决：TP 10 / 舍入过判 9 /
FP 68，漏报实证 2 类。本文件是 MR-P1~P6 六个批次的机检钉线：

- 每批次一组**合成正例/反例**单元测试（修复的逻辑行为本身）；
- **TP 保留钉线**：把裁决的 10 条 TP 写成机检断言
  （键 = 规则 × 材料 × 关键数字，容差沿用 classify_amount_diff）——
  防止「修没真阳性」。语料 PDF 不入 git（bench fail-closed 纪律），
  PDF 缺失的干净 checkout 上显式 skip，探针重放（run_benchmark --doc）
  是 TP 的主验收面；
- FP 清零的逐批验收走 docs/baselines/bench_probe14_metrics_*.json 快照对比。

红线（MR-P 方案 §〇）：不删义务凑绿；FP 不得改装成 unresolved 充数；
10 条 TP 一条不许修丢。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.engine.rule_outcome import RuleDeferred  # noqa: E402
from src.engine.rules_v33 import (  # noqa: E402
    R33005_TableTotalConsistency,
    R33200_InterTable_T1_T2,
    R33201_InterTable_T1_T3,
    R33233_DetailRowFormulaConsistency,
    R33240_Table2_IncomeAdvancedCheck,
    R33241_Table3_ExpenseAdvancedCheck,
    R33TrendCompletionRate,
    _two_sided_side_value,
    build_document,
)

# ---------------------------------------------------------------------------
# 工具：合成文档 / 语料文档装载（PDF 缺失 → skip，不静默放空）
# ---------------------------------------------------------------------------


def _doc_from_pages(
    *pages: str, path: str = "contract-mrp.pdf"
) -> Any:
    """合成 final 文档：页 1 提供年度锚，其后每参量一页。"""
    return build_document(
        path=path,
        page_texts=["某某局 2025 年度部门决算公开", *pages],
        page_tables=[[]],
        filesize=0,
    )


def _load_corpus_doc(doc_id: str) -> Any:
    """装载已登记语料 PDF（本机已登记的 14 份探针观察面）。

    语料 PDF 不入 git：干净 checkout 上 skip（探针重放是主验收面，
    不在无源环境伪装通过）。
    """
    pdf = ROOT / "corpus" / doc_id / "sample.pdf"
    if not pdf.exists():
        pytest.skip(f"语料 PDF 不在本机（干净 checkout）：{doc_id}")
    from scripts.replay_golden_corpus import load_page_tables, load_page_texts

    return build_document(
        str(pdf),
        load_page_texts(pdf),
        load_page_tables(pdf),
        1000,
    )


def _doc_with_table(
    table_name: str, rows: List[List[str]], extra_pages: Optional[Dict[int, List[List[List[str]]]]] = None
) -> Any:
    """构造可锚定的单表文档：页 2 文本含表名（锚点），页表数据挂对应页。"""
    page_tables: List[List[List[List[str]]]] = [[], [rows]]
    if extra_pages:
        for pg, tbls in extra_pages.items():
            while len(page_tables) < pg:
                page_tables.append([])
            page_tables[pg - 1] = tbls
    return build_document(
        path="contract-mrp-table.pdf",
        page_texts=["某某局 2025 年度部门决算公开", table_name],
        page_tables=page_tables,
        filesize=0,
    )


# ---------------------------------------------------------------------------
# MR-P1：V33-200/201 总表收支列身份 fail-closed
# ---------------------------------------------------------------------------

#: 双栏总表标准形态（上海市版式）：表头两行 + 收支行 + 支出行
_TWO_SIDED_T1 = [
    ["收入", "", "支出", ""],
    ["项目", "决算数", "项目", "决算数"],
    ["一、一般公共预算财政拨款收入", "2,171,700.66", "一、一般公共服务支出", "1,982.47"],
    ["本年收入合计", "2,418,329.38", "本年支出合计", "2,443,579.41"],
    ["年初结转和结余", "48,688.30", "年末结转和结余", "23,409.06"],
    ["总计", "2,468,832.94", "总计", "2,468,832.94"],
]


def test_p1_two_sided_value_takes_label_side():
    """双栏行按标签所在半行取数：收入侧 2,418,329.38、支出侧 2,443,579.41
    （探针 009 p8 实证数）。旧 max(vals) 在支出>收入时把支出侧当收入。"""
    rows = _TWO_SIDED_T1
    assert _two_sided_side_value(rows, "本年收入合计", "income") == 2418329.38
    assert _two_sided_side_value(rows, "本年支出合计", "expense") == 2443579.41


def test_p1_two_sided_value_single_sided_row_unaffected():
    """单侧布局（标签+单值）行为不变：取标签后第一个数值。"""
    rows = [
        ["项目", "决算数"],
        ["本年收入合计", "100.00"],
    ]
    assert _two_sided_side_value(rows, "本年收入合计", "income") == 100.0


def test_p1_two_sided_value_fails_closed_when_label_side_empty():
    """标签所在半行无数值 → None（fail-closed），不得取另一侧的数凑数。"""
    rows = [
        ["收入", "", "支出", ""],
        ["项目", "决算数", "项目", "决算数"],
        ["本年收入合计", "", "本年支出合计", "200.00"],
    ]
    assert _two_sided_side_value(rows, "本年收入合计", "income") is None
    assert _two_sided_side_value(rows, "本年支出合计", "expense") == 200.0


def test_p1_two_sided_value_header_side_conflict_fails_closed():
    """表头侧身份与标签所在半行冲突 → None（列身份校验，禁止混猜）。"""
    # 表头声明收入在左、支出在右；标签「本年支出合计」却出现在左半行
    rows = [
        ["收入", "", "支出", ""],
        ["项目", "决算数", "项目", "决算数"],
        ["本年支出合计", "2,443,579.41", "", ""],
    ]
    assert _two_sided_side_value(rows, "本年支出合计", "expense") is None


def test_p1_v33_200_reports_real_income_mismatch():
    """正例保留：T1 收入合计与 T2 真实不一致仍报 error（不因修复变哑）。

    T2 收入表合计行取 2,300,000.00 ≠ T1 收入侧 2,418,329.38。修复前该形态
    也会报（max 恰好取对侧更大数），修复后按列身份取数仍必须报——
    防止「fail-closed 把真差异也吞掉」。"""
    t1 = [row[:] for row in _TWO_SIDED_T1]
    t2 = [
        ["项目", "本年收入合计", "财政拨款收入", "其他收入"],
        ["功能分类科目编码", "科目名称", "", ""],
        ["合计", "", "2,300,000.00", "2,300,000.00"],
    ]
    doc = build_document(
        path="p1-real-mismatch.pdf",
        page_texts=[
            "某某局 2025 年度部门决算公开",
            "一、收入支出决算总表",
            "二、收入决算表",
        ],
        page_tables=[[], [t1], [t2]],
        filesize=0,
    )
    issues = R33200_InterTable_T1_T2().apply(doc)
    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert "T1=2418329.38" in issues[0].message
    assert "T2=2300000.00" in issues[0].message


def test_p1_v33_201_reports_real_expense_mismatch():
    """正例保留：T1 支出侧与 T3 真实不一致仍报 error。"""
    t1 = [row[:] for row in _TWO_SIDED_T1]
    t3 = [
        ["项目", "本年支出合计", "基本支出", "项目支出"],
        ["功能分类科目编码", "科目名称", "", ""],
        ["合计", "", "2,000,000.00", "2,000,000.00"],
    ]
    doc = build_document(
        path="p1-real-expense-mismatch.pdf",
        page_texts=[
            "某某局 2025 年度部门决算公开",
            "一、收入支出决算总表",
            "三、支出决算表",
        ],
        page_tables=[[], [t1], [t3]],
        filesize=0,
    )
    issues = R33201_InterTable_T1_T3().apply(doc)
    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert "T1=2443579.41" in issues[0].message


def test_p1_v33_201_side_value_on_probe_shape():
    """019 形态：支出侧取 130,442.55（旧实现取收入侧 195,989.75 报假不一致）。"""
    rows = [
        ["收入", "", "支出", ""],
        ["项目", "决算数", "项目", "决算数"],
        ["本年收入合计", "195,989.75", "本年支出合计", "130,442.55"],
        ["年初结转和结余", "45,830.18", "年末结转和结余", "110,757.63"],
        ["总计", "241,860.77", "总计", "241,860.77"],
    ]
    assert _two_sided_side_value(rows, "本年支出合计", "expense") == 130442.55
    assert _two_sided_side_value(rows, "本年收入合计", "income") == 195989.75


# ---------------------------------------------------------------------------
# MR-P2：V33-TREND-COMPLETION-RATE 分子/分母同科目绑定（其中：总-分层级纪律）
# ---------------------------------------------------------------------------


def _san_gong_page(item_budget_clause: str = "") -> Any:
    """三公总体情况说明的真实句式形态（探针 009/019 版式）。

    item_budget_clause 为空 → 分项无自身预算披露（fail-closed 形态）；
    非空 → 分项带自身预算（正向绑定形态）。
    """
    return _doc_from_pages(
        "七、财政拨款“三公”经费支出决算情况说明\n"
        "（一）“三公”经费财政拨款支出决算总体情况说明。\n"
        "“三公”经费财政拨款支出年初预算为 1,435.72 万元，支出决算为\n"
        "1,158.99 万元，完成预算的 80.72%，其中：因公出国（境）费"
        f"{item_budget_clause}决算为 370.23 万元，完成预算的 80.92%；\n"
        "公务用车购置及运行维护费支出决算为 754.61 万元，完成预算的 88.50%；\n"
        "公务接待费支出决算为 34.15 万元，完成预算的 27.20%。"
    )


def test_p2_subitem_rate_not_bound_to_total_budget():
    """反例（探针 009 实证形态）：分项完成率 80.92% 的分项预算未在叙事
    披露时，不得把「其中：」之前的总句年初预算 1,435.72 拼给分项声明
    （旧实现复算 25.79% 报假矛盾）。总-分层级配对不齐 → 不报。"""
    assert R33TrendCompletionRate().apply(_san_gong_page()) == []


def test_p2_subitem_rate_binds_own_budget_after_where():
    """正例：分项自身预算在「其中：」之后披露时照常复算；写错必报。"""
    doc = _san_gong_page("年初预算为 457.55 万元，")
    issues = R33TrendCompletionRate().apply(doc)
    # 370.23/457.55 = 80.914…% ≈ 80.92% → 数学一致，0 finding
    assert issues == [], [getattr(i, "message", "") for i in issues]


def test_p2_subitem_rate_wrong_own_budget_is_reported():
    """正例（防哑）：分项预算披露后完成率写错 → 恰好 1 条 error，且
    复算按分项自身分母（457.55），不是总句 1,435.72。"""
    doc = _san_gong_page("年初预算为 457.55 万元，")
    # 改完成率为 30%（≈370.23/1435.72 的假口径）→ 必须报，recomputed≈80.91
    doc.page_texts[-1] = doc.page_texts[-1].replace("80.92%", "30.00%")
    issues = R33TrendCompletionRate().apply(doc)
    assert len(issues) == 1
    assert issues[0].severity == "error"
    # 复算按分项自身分母 457.55（370.23/457.55=80.92%），不是总句 1,435.72（25.79%）
    assert "80.92" in str(issues[0].location.get("recomputed_completion_rate", ""))
    assert "457.55" in str(issues[0].location.get("denom_amount", "")) or "457.55" in str(issues[0].message)


def test_p2_total_rate_claim_still_binds_total_disclosures():
    """总句完成率 80.72% 与总句预算/决算同句绑定不受影响（1,158.99/1,435.72
    = 80.72% 材料自洽；总句是设计内正例）。"""
    doc = _san_gong_page()
    # 单独放大总句为错误口径验证总句仍被复算：把 80.72% 改成 90%
    doc.page_texts[-1] = doc.page_texts[-1].replace("80.72%", "90.00%")
    issues = R33TrendCompletionRate().apply(doc)
    assert any("90.00" in str(getattr(i, "location", {}).get("declared_completion_rate", "")) for i in issues)


# ---------------------------------------------------------------------------
# MR-P3a：V33-005 跨页断裂/无头碎片 + V33-233 列身份
# ---------------------------------------------------------------------------


def test_p3a_headless_fragment_detection():
    """无头碎片判定：首行首格为数值 → True；带表头关键词 → False。"""
    rule = R33005_TableTotalConsistency()
    assert rule._is_headless_table(
        [["2140112", "公路运输管理", "24,758.24", "15,265.70", "9,492.53"]]
    )
    assert not rule._is_headless_table(
        [["项目", "", "本年支出合计", "基本支出", "项目支出"]]
    )
    assert not rule._is_headless_table([])
    assert not rule._is_headless_table([[]])


def test_p3a_v33_005_fragment_merged_into_whole_table():
    """正例（探针 009 p23 形态）：跨页续页碎片不再单独检查——整表按
    anchor_extent 合并后自洽 → 0 finding（旧实现报「列：15,265.70」假不平）。"""
    header = ["功能分类科目编码", "科目名称", "本年支出合计", "基本支出", "项目支出"]
    anchor_rows = [
        header,
        ["功能分类科目编码", "科目名称", "", "", ""],
        ["201", "一般公共服务支出", "300.00", "100.00", "200.00"],
        ["合计", "", "400.00", "150.00", "250.00"],
    ]
    fragment = [
        ["202", "外交支出", "100.00", "50.00", "50.00"],
        ["合计", "", "500.00", "200.00", "300.00"],
    ]
    doc = build_document(
        path="p3a-whole-table.pdf",
        page_texts=["某某局 2025 年度部门决算公开", "五、支出决算表", "（续表）"],
        page_tables=[[], [anchor_rows], [fragment]],
        filesize=0,
    )
    issues = R33005_TableTotalConsistency().apply(doc)
    assert issues == [], [getattr(i, "message", "") for i in issues]


def test_p3a_v33_005_real_whole_table_mismatch_still_reported():
    """正例（防哑）：整表真实不平仍报 error，且归并为整表 1 条（碎片页
    不再重复报）。"""
    anchor_rows = [
        ["功能分类科目编码", "科目名称", "本年支出合计", "基本支出", "项目支出"],
        ["201", "一般公共服务支出", "100.00", "100.00", "0.00"],
        ["合计", "", "500.00", "100.00", "400.00"],
    ]
    fragment = [["202", "外交支出", "10.00", "10.00", "0.00"]]
    doc = build_document(
        path="p3a-real-mismatch.pdf",
        page_texts=["某某局 2025 年度部门决算公开", "五、支出决算表", "（续表）"],
        page_tables=[[], [anchor_rows], [fragment]],
        filesize=0,
    )
    issues = R33005_TableTotalConsistency().apply(doc)
    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert "支出决算表" in issues[0].message


def test_p3a_v33_233_narrow_continuation_rows_read_adaptively():
    """正例（探针 013 p15 形态）：锚定页 10 列、续页 8 列——窄行按行自适应
    取列，1,113.39=1,106.19+7.20 自洽 → 0 finding（旧实现把名称读成
    1,106.19、合计读成 7.20 报 4 条假勾稽）。"""
    wide_header = [
        ["项目", "", "", "", "本年支出合计", "基本支出", "项目支出", "上缴上级支出", "经营支出", "对附属单位补助支出"],
        ["功能分类科目编码", "", "", "科目名称", "", "", "", "", "", ""],
        ["类", "款", "项", "合计", "决算数", "决算数", "决算数", "决算数", "决算数", "决算数"],
    ]
    rows = [
        *wide_header,
        ["201", "", "", "一般公共服务支出", "300.00", "200.00", "100.00", "0.00", "0.00", "0.00"],
        ["210", "卫生健康支出", "1,113.39", "1,106.19", "7.20", "0.00", "0.00", "0.00"],
    ]
    t5_rows = [
        ["项目", "", "本年支出合计", "基本支出", "项目支出"],
        ["功能分类科目编码", "科目名称", "", "", ""],
        ["201", "一般公共服务支出", "300.00", "200.00", "100.00"],
    ]
    doc = build_document(
        path="p3a-narrow-rows.pdf",
        page_texts=[
            "某某局 2025 年度部门决算公开",
            "三、支出决算表",
            "五、一般公共预算财政拨款支出决算表",
        ],
        page_tables=[[], [rows], [t5_rows]],
        filesize=0,
    )
    issues = R33233_DetailRowFormulaConsistency().apply(doc)
    assert issues == [], [getattr(i, "message", "") for i in issues]


def test_p3a_v33_233_unresolved_headers_fail_closed():
    """反例：表头关键词定位不到 合计/基本支出/项目支出 → RuleDeferred
    （fail-closed），不再按位置兜底取列。"""
    rows = [
        ["甲", "乙", "丙"],
        ["210", "卫生健康支出", "1,113.39"],
    ]
    doc = build_document(
        path="p3a-no-headers.pdf",
        page_texts=["某某局 2025 年度部门决算公开", "三、支出决算表"],
        page_tables=[[[], rows]],
        filesize=0,
    )
    with pytest.raises(RuleDeferred):
        R33233_DetailRowFormulaConsistency().apply(doc)


# ---------------------------------------------------------------------------
# MR-P3b：V33-240/241 口径列补全 + 纵向漏行 fail-closed
# ---------------------------------------------------------------------------


def test_p3b_v33_241_extra_scope_columns_close_horizontal():
    """正例（探针 026 形态）：合计=基本+项目+经营支出 38.26 表自洽 →
    横向公式纳入口径列后 0 finding（旧实现报 4 条假不平）。"""
    rows = [
        ["项目", "", "本年支出合计", "基本支出", "项目支出", "上缴上级支出", "经营支出", "对附属单位补助支出"],
        ["功能分类科目编码", "科目名称", "", "", "", "", "", ""],
        ["合计", "", "112,586.95", "47,927.50", "64,621.18", "", "38.26", ""],
        ["201", "一般公共服务支出", "112,586.95", "47,927.50", "64,621.18", "", "38.26", ""],
    ]
    doc = build_document(
        path="p3b-extra-cols.pdf",
        page_texts=["某某局 2025 年度部门决算公开", "三、支出决算表"],
        page_tables=[[], [rows]],
        filesize=0,
    )
    issues = R33241_Table3_ExpenseAdvancedCheck().apply(doc)
    # 口径列纳入后勾稽成立：无 error；0.01 级浮点尾差按 MR-P4 降为聚类 info
    errors = [i for i in issues if str(i.severity).lower() == "error"]
    assert errors == [], [getattr(i, "message", "") for i in errors]
    assert all("舍入包络差异" in str(i.message) for i in issues)


def test_p3b_v33_240_genuine_horizontal_mismatch_still_reported():
    """正例（防哑，探针 019 形态）：合计列漏加其他收入 14.05/13.00 的真实
    横向不平必须保留为 error（V33-240 家族最重要的正向钉线）。"""
    t2_rows = [
        ["项目", "本年收入合计", "财政拨款收入", "上级补助收入", "事业收入", "经营收入", "附属单位上缴收入", "其他收入"],
        ["功能分类科目编码", "科目名称", "", "", "", "", "", ""],
        ["合计", "141009.68", "140995.63", "0.00", "0.00", "0.00", "0.00", "14.05"],
        ["201", "140995.63", "140995.63", "0.00", "0.00", "0.00", "0.00", "14.05"],
        ["206", "14.05", "0.00", "0.00", "0.00", "0.00", "0.00", "14.05"],
    ]
    doc = build_document(
        path="p3b-real-horizontal.pdf",
        page_texts=["某某局 2025 年度部门决算公开", "二、收入决算表"],
        page_tables=[[], [t2_rows]],
        filesize=0,
    )
    issues = R33240_Table2_IncomeAdvancedCheck().apply(doc)
    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert "14.05" in issues[0].message


def test_p3b_vertical_mismatch_with_text_visible_missing_code_defers():
    """反例（探针 009 形态）：款金额 ≠ 项之和，但缺失项的编码在页文本
    可见（跨页漏行）→ RuleDeferred fail-closed，不报 error。"""
    t3_rows = [
        ["项目", "", "本年支出合计", "基本支出", "项目支出"],
        ["功能分类科目编码", "科目名称", "", "", ""],
        ["21401", "公路水路运输", "348,979.41", "51,126.93", "297,852.48"],
        ["2140101", "行政运行", "31,075.00", "30,995.60", "79.40"],
    ]
    doc = build_document(
        path="p3b-missing-row.pdf",
        page_texts=[
            "某某局 2025 年度部门决算公开",
            "三、支出决算表",
            "2140106 公路养护 193,201.13 0.00 193,201.13",
        ],
        page_tables=[[], [t3_rows], []],
        filesize=0,
    )
    with pytest.raises(RuleDeferred) as excinfo:
        R33241_Table3_ExpenseAdvancedCheck().apply(doc)
    assert "2140106" in str(excinfo.value.detail)


def test_p3b_vertical_mismatch_without_missing_code_still_reported():
    """正例（探针 020 类级 208 形态）：文本与表格一致、无漏行证据时，
    纵向失衡照常报 error（不得以 fail-closed 吞掉真实不平）。"""
    t3_rows = [
        ["项目", "", "本年支出合计", "基本支出", "项目支出"],
        ["功能分类科目编码", "科目名称", "", "", ""],
        ["208", "社会保障和就业支出", "164,977.86", "95,823.79", "69,154.06"],
        ["20805", "行政事业单位养老支出", "15,366.12", "15,366.12", "0.00"],
        ["2080501", "行政单位离退休", "15,366.12", "15,366.12", "0.00"],
    ]
    doc = build_document(
        path="p3b-real-vertical.pdf",
        page_texts=["某某局 2025 年度部门决算公开", "三、支出决算表"],
        page_tables=[[], [t3_rows], []],
        filesize=0,
    )
    issues = R33241_Table3_ExpenseAdvancedCheck().apply(doc)
    assert any("纵向汇总不平" in getattr(i, "message", "") for i in issues)


# ---------------------------------------------------------------------------
# TP 保留钉线：10 条真阳性（键 = 规则 × 材料 × 关键数字）
# ---------------------------------------------------------------------------
