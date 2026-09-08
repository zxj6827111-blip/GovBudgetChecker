"""受影响规则专项测试矩阵（审查质量整改 2026-09-05）。

对照计划验收项「每条受影响规则增加正例、反例、软换行、双栏、跨页、
舍入边界和解析不足测试」，逐规则覆盖：

- CMM-002 / CMM-004：正例、反例（软换行/科目域）
- V33-001：年度缺位正例、同比语境反例、结构性年份冲突正例
- V33-106 / V33-110 / V33-220 / V33-244：说明归因（软换行恢复、
  年份 token 排除、跨句错配反例、真不一致正例）
- V33-115 / V33-117 / V33-120：双栏、跨页列宽重映射、舍入边界、
  解析不足（None 安全）与真实错误
- V33-245 / V33-246：新规则正反例
- detect_table_code：FIN_05 特异性 tie-break（识别缺口修复）
"""

from __future__ import annotations

import pytest

from src.engine.pipeline import build_document
from src.engine.rule_outcome import RuleNotApplicable
from src.engine.rules_v33 import (
    R33001_CoverYearUnit,
    R33106_GeneralBudgetStruct,
    R33110_BudgetVsFinal_TextConsistency,
    R33115_TotalSheetCheck,
    R33117_BasicExpenseClassification,
    R33120_DetailTableCheck,
    R33101_TotalSheet_Identity,
)
from src.engine.common_rules import CMM002_TextAnomalyRule, CMM004_CodeMirrorConsistency
from src.engine.rules_v33 import (
    R33220_Narrative3_T3,
    R33244_Table7_ThreePublicAdvancedCheck,
    R33245_ThreePublicDirectionContradiction,
    R33246_DomesticReceptionDisclosure,
)
from src.services.fiscal_table_rules import detect_table_code


def make_doc(page_texts, page_tables):
    """page_tables: 与 page_texts 等长，每元素为该页的表格（rows）或 None；
    内部包装成管线约定的一页多表结构 [[table], ...]。"""
    wrapped = [[t] if t else [] for t in page_tables]
    return build_document(
        path="synthetic.pdf",
        page_texts=page_texts,
        page_tables=wrapped,
        filesize=0,
    )


def rules_of(issues):
    return [str(getattr(i, "rule", "")) for i in issues]


def single_page_table(text, table):
    return [text], [table]


# ---------------------------------------------------------------------------
# CMM-004 科目域隔离
# ---------------------------------------------------------------------------


def test_cmm004_same_domain_mismatch_hits():
    """正例：收入侧与支出侧同域编码金额不一致 → 命中。"""
    text = (
        "收入决算表\n"
        "201 一般公共服务支出 100.00\n"
        "203 海关事务支出 50.00\n"
    )
    text2 = (
        "支出决算表\n"
        "201 一般公共服务支出 100.00\n"
        "203 海关事务支出 40.00\n"
    )
    doc = make_doc([text, text2], [[], []])
    issues = CMM004_CodeMirrorConsistency().apply(doc)
    assert any("201" in str(i.message) or "203" in str(i.message) for i in issues)


def test_cmm004_domain_mismatch_is_not_applicable():
    """反例：收入分类 vs 经济分类 → not_applicable，不产 finding（样张 33 条误报根因）。"""
    text = (
        "收入决算表\n"
        "101 税收收入 500.00\n"
        "102 社会保险基金收入 300.00\n"
    )
    text2 = (
        "支出决算表\n"
        "301 工资福利支出 200.00\n"
        "302 商品和服务支出 100.00\n"
    )
    doc = make_doc([text, text2], [[], []])
    with pytest.raises(RuleNotApplicable):
        CMM004_CodeMirrorConsistency().apply(doc)


def test_cmm004_mixed_domain_is_not_applicable():
    """反例：单侧域混杂（功能+经济编码混在支出侧）→ not_applicable。"""
    text = (
        "收入决算表\n"
        "201 一般公共服务支出 100.00\n"
        "203 海关事务支出 50.00\n"
    )
    text2 = (
        "支出决算表\n"
        "301 工资福利支出 200.00\n"
        "302 商品和服务支出 100.00\n"
        "310 资本性支出 20.00\n"
    )
    doc = make_doc([text, text2], [[], []])
    with pytest.raises(RuleNotApplicable):
        CMM004_CodeMirrorConsistency().apply(doc)


# ---------------------------------------------------------------------------
# CMM-002 软换行 + 段落级引号
# ---------------------------------------------------------------------------


def test_cmm002_softwrapped_quote_pair_not_flagged():
    """反例：引号短语被 PDF 软换行拆开（样张 4 条误报根因）→ 不命中。"""
    text = (
        "主要用于开展《美丽普陀建设“十\n"
        "五五”规划》编制工作。年初预算为0.00 万元。\n"
    )
    doc = make_doc([text], [[]])
    assert CMM002_TextAnomalyRule().apply(doc) == []


def test_cmm002_real_unclosed_quote_hits():
    """正例：段落内右引号确实多于左引号 → 命中。"""
    text = "本单位深入推进”放管服”改革工作。相关说明如下。\n"
    doc = make_doc([text], [[]])
    issues = CMM002_TextAnomalyRule().apply(doc)
    assert any("引号" in str(i.message) for i in issues)


# ---------------------------------------------------------------------------
# V33-001 年度
# ---------------------------------------------------------------------------


def test_v33001_toc_incomplete_year_hits():
    """正例：目录「202 年度」占位残留（T1）→ 命中并定位目录页。"""
    doc = make_doc(
        [
            "上海市某局 2025 年度部门决算",
            "目 录\n第三部分某局 202 年度部门决算情况说明",
        ],
        [[], []],
    )
    issues = R33001_CoverYearUnit().apply(doc)
    assert any("年度缺位" in str(i.message) for i in issues)


def test_v33001_yoy_context_not_flagged():
    """反例：正文的同比表述（与2024年度相比增加）不参与年份冲突判定。

    同比句放在第 4 页（封面/目录/表头结构性位置之外）——这是
    "正文同比不参与冲突判定"的真实语义。
    """
    doc = make_doc(
        [
            "上海市某局 2025 年度部门决算",
            "目 录",
            "第一部分 部门概况",
            "说明：支出决算数与2024年度相比增加 90.09 万元，增长 42.68%。",
        ],
        [[], [], [], []],
    )
    issues = R33001_CoverYearUnit().apply(doc)
    assert not any("多个年份" in str(i.message) or "年份混淆" in str(i.message) for i in issues)


def test_v33001_structural_year_conflict_hits():
    """正例：封面 2024 与目录 2025 结构性冲突 → 命中。"""
    doc = make_doc(
        [
            "上海市某局 2024 年度部门决算",
            "目 录\n第一部分 2025 年度部门决算表",
        ],
        [[], []],
    )
    issues = R33001_CoverYearUnit().apply(doc)
    assert any("年份混淆" in str(i.message) or "多个年份" in str(i.message) for i in issues)


# ---------------------------------------------------------------------------
# V33-115 双栏总表
# ---------------------------------------------------------------------------


def _total_sheet_table(income_total, expense_total):
    return [
        ["收入", "", "支出", ""],
        ["项目", "决算数", "项目", "决算数"],
        ["一、一般公共预算财政拨款收入", "4,733.14", "一、一般公共服务支出", "4,733.14"],
        ["本年收入合计", "4,733.14", "本年支出合计", "4,733.14"],
        ["年初结转和结余", "0.00", "结余分配", "0.00"],
        ["收入总计", f"{income_total}", "支出总计", f"{expense_total}"],
    ]


def test_v33115_balanced_two_sided_table_passes():
    """正例（双栏）：收入总计=支出总计、两侧平衡 → 无 finding。"""
    doc = make_doc(
        ["收入支出决算总表"],
        [_total_sheet_table("4,733.14", "4,733.14")],
    )
    assert R33115_TotalSheetCheck().apply(doc) == []


def test_v33115_imbalance_hits_error():
    """反例：收支总计真实不平 → error。"""
    doc = make_doc(
        ["收入支出决算总表"],
        [_total_sheet_table("4,733.14", "4,700.00")],
    )
    issues = R33115_TotalSheetCheck().apply(doc)
    assert any("总表平衡性错误" in str(i.message) for i in issues)


def test_v33115_rounding_difference_is_info():
    """舍入边界：差 0.01（≤ 包络 0.01）→ info 提示，不报 error。"""
    doc = make_doc(
        ["收入支出决算总表"],
        [_total_sheet_table("4,733.14", "4,733.13")],
    )
    issues = R33115_TotalSheetCheck().apply(doc)
    assert any(i.severity == "info" and "取整误差" in str(i.message) for i in issues)


def test_v33115_missing_component_does_not_fake_error():
    """解析不足：缺少「使用非财政拨款结余」等分量行 → 跳过该侧校验，
    不把行标签转成 0.0 伪造「计算值(0.0)」假 error（样张 V33-115 误报根因）。"""
    table = [
        ["收入", "", "支出", ""],
        ["项目", "决算数", "项目", "决算数"],
        ["收入总计", "4,733.14", "支出总计", "4,733.14"],
    ]
    doc = make_doc(["收入支出决算总表"], [table])
    issues = R33115_TotalSheetCheck().apply(doc)
    assert not any("平衡错误" in str(i.message) and "0.0)" in str(i.message) for i in issues)


# ---------------------------------------------------------------------------
# V33-117 经济分类（双栏、款级防护、舍入边界）
# ---------------------------------------------------------------------------


def _basic_expense_table(class_310="14.44", child_310_a="13.94", child_310_b="0.49"):
    """双栏经济分类布局：左栏 301/303（人员），右栏 302/310（公用）。

    类级金额与款级明细自洽（301=1000.00+1857.05，302=92.11+309.82，
    303=91.97），310 类=13.94+0.49+class_310 的偏差由参数控制——
    默认 class_310=14.44 与明细和 14.43 差 0.01，复刻样张 T4 场景。
    """
    return [
        ["经济分类科目编码", "", "科目名称", "决算数", "经济分类科目编码", "", "科目名称", "决算数"],
        ["类", "款", "", "", "类", "款", "", ""],
        ["301", "", "工资福利支出", "2,857.05", "302", "", "商品和服务支出", "401.93"],
        ["301", "01", "基本工资", "1,000.00", "302", "01", "办公费", "92.11"],
        ["301", "02", "津贴补贴", "1,857.05", "302", "99", "其他商品和服务支出", "309.82"],
        ["303", "", "对个人和家庭的补助", "91.97", "310", "", "资本性支出", class_310],
        ["303", "01", "退休费", "91.97", "310", "99", "其他资本性支出", child_310_a],
        ["", "", "", "", "310", "02", "大型修缮", child_310_b],
        ["人员经费合计", "", "", "2,949.02", "公用经费合计", "", "", "416.36"],
    ]


def test_v33117_balanced_economic_table_passes():
    """正例（双栏）：人员/公用类级之和与显式合计一致 → 无 finding。"""
    doc = make_doc(
        ["一般公共预算财政拨款基本支出决算表"],
        [_basic_expense_table("14.43", "13.94", "0.49")],
    )
    issues = R33117_BasicExpenseClassification().apply(doc)
    assert [i for i in issues if i.severity in ("error", "warn")] == []


def test_v33117_subtotal_rows_not_double_counted():
    """反例防护：款级明细行不得重复计入类级合计（样张 3309.02 假累加根因）。"""
    doc = make_doc(
        ["一般公共预算财政拨款基本支出决算表"],
        [_basic_expense_table()],
    )
    issues = R33117_BasicExpenseClassification().apply(doc)
    # 类级之和 = 302(401.93)+310(14.44)=416.37 vs 显式 416.36 → 0.01 舍入 info；
    # 若款级行被错误累加会出现巨大 mismatch（warn/error）
    assert not any(i.severity in ("error", "warn") for i in issues)


def test_v33117_rounding_hint_on_class_row():
    """舍入边界：310 类 14.44 vs 明细和 14.43（差 0.01 ≤ 包络）→ info（T4）。"""
    doc = make_doc(
        ["一般公共预算财政拨款基本支出决算表"],
        [_basic_expense_table()],
    )
    issues = R33117_BasicExpenseClassification().apply(doc)
    assert any(
        i.severity == "info" and "310" in str(i.message) for i in issues
    ), [str(i.message) for i in issues]


def test_v33117_real_mismatch_hits_warn():
    """反例：类级之和与显式合计真实不符 → warn。"""
    doc = make_doc(
        ["一般公共预算财政拨款基本支出决算表"],
        [_basic_expense_table(class_310="20.00", child_310_a="13.94", child_310_b="0.49")],
    )
    issues = R33117_BasicExpenseClassification().apply(doc)
    assert any(i.severity == "warn" for i in issues)


# ---------------------------------------------------------------------------
# V33-120 跨页列宽重映射 + 列合计
# ---------------------------------------------------------------------------


def _expense_table_page1():
    """宽行页（3 列编码 + 名称 + 3 金额列）。"""
    return [
        ["项目", "", "", "", "本年支出合计", "基本支出", "项目支出"],
        ["功能分类科目编码", "", "", "科目名称", "", "", ""],
        ["类", "款", "项", "合计", "4,733.14", "3,365.38", "1,367.76"],
        ["201", "", "", "一般公共服务支出", "474.40", "400.00", "74.40"],
        ["211", "", "", "节能环保支出", "2,199.25", "2,100.00", "99.25"],
    ]


def _expense_table_page2_narrow():
    """续页窄行（7 位编码合并为一列）——样张 P10→P11 的跨页列宽漂移。"""
    return [
        ["2110101", "行政运行", "1,293.60", "1,200.00", "93.60", "0.00", "0.00", "0.00"],
        ["2110301", "污染防治", "623.06", "600.00", "23.06", "0.00", "0.00", "0.00"],
        ["2110402", "生态环境监测与信息", "88.60", "80.00", "8.60", "0.00", "0.00", "0.00"],
    ]


def test_v33120_crosspage_narrow_rows_remapped_no_fake_hierarchy():
    """跨页列宽漂移：窄行按逻辑列重映射后类=Σ款应平衡，不得产
    「父级金额(0.0) != 子级之和」假 warn（样张 4 条误报根因）。"""
    doc = make_doc(
        ["支出决算表", "支出决算表（续）"],
        [_expense_table_page1(), _expense_table_page2_narrow()],
    )
    issues = R33120_DetailTableCheck().apply(doc)
    assert not any("0.0)" in str(i.message) for i in issues)


def test_v33120_column_sum_rounding_hint():
    """舍入边界（T2）：合计行与最低级明细列和差 0.01 → info。"""
    doc = make_doc(
        ["支出决算表"],
        [[
            ["项目", "", "", "", "本年支出合计", "基本支出", "项目支出"],
            ["功能分类科目编码", "", "", "科目名称", "", "", ""],
            ["类", "款", "项", "合计", "1,367.76", "0.00", "1,367.76"],
            ["201", "", "", "一般公共服务支出", "474.40", "0.00", "474.40"],
            ["211", "", "", "节能环保支出", "893.36", "0.00", "893.35"],
        ]],
    )
    issues = R33120_DetailTableCheck().apply(doc)
    assert any(i.severity == "info" and "取整误差" in str(i.message) for i in issues), [
        str(i.message) for i in issues
    ]


def test_v33120_real_column_mismatch_hits_warn():
    """反例：合计行与明细之和真实不符 → warn。"""
    doc = make_doc(
        ["支出决算表"],
        [[
            ["项目", "", "", "", "本年支出合计", "基本支出", "项目支出"],
            ["功能分类科目编码", "", "", "科目名称", "", "", ""],
            ["类", "款", "项", "合计", "1,500.00", "0.00", "1,500.00"],
            ["201", "", "", "一般公共服务支出", "474.40", "0.00", "474.40"],
            ["211", "", "", "节能环保支出", "893.36", "0.00", "893.35"],
        ]],
    )
    issues = R33120_DetailTableCheck().apply(doc)
    assert any(i.severity == "warn" for i in issues)


# ---------------------------------------------------------------------------
# V33-106 / V33-110 / V33-220 说明归因
# ---------------------------------------------------------------------------


def test_v33106_year_token_not_treated_as_amount():
    """反例（年份 token）：说明里的「2025年度」不得被当成金额与表比较
    （样张「说明数字(2025.00)」误报根因）。"""
    text = "五、一般公共预算财政拨款支出决算情况说明\n2025年度年初预算为4,628.17万元，支出决算为4,733.14万元。"
    doc = make_doc([text], [[]])
    issues = R33106_GeneralBudgetStruct().apply(doc)
    assert not any("2025.00" in str(i.message) for i in issues)


def test_v33110_cross_sentence_pairing_not_flagged():
    """反例（跨句错配）：总结句与分项金额不得交叉配对
    （样张「预算数4628.17，决算数51.98」假 error 根因）。"""
    text = (
        "（三）一般公共预算财政拨款支出决算情况说明\n"
        "一般公共预算财政拨款支出年初预算为4,628.17万元，支出决算为4,733.14万元，完成年初预算的102.27%。决算数大于预算数。\n"
        "1、行政运行（项）。年初预算为51.98万元，支出决算为51.98万元，完成年初预算的100%。与上年相比无增长变化。\n"
    )
    doc = make_doc([text], [[]])
    assert R33110_BudgetVsFinal_TextConsistency().apply(doc) == []


def test_v33110_real_direction_mismatch_hits():
    """正例：金额关系与文字表述真实矛盾 → error。"""
    text = (
        "（三）一般公共预算财政拨款支出决算情况说明\n"
        "一般公共预算财政拨款支出年初预算为4,733.14万元，支出决算为4,628.17万元。决算数大于预算数。\n"
    )
    doc = make_doc([text], [[]])
    issues = R33110_BudgetVsFinal_TextConsistency().apply(doc)
    assert any(i.severity == "error" for i in issues)


def test_v33220_narrative_table_consistent_passes():
    """正例：说明三的基本/项目支出与 T3 合计行一致 → 无 finding。"""
    text = "三、支出决算情况说明\n本年支出合计4,733.14万元，其中基本支出3,365.38万元，占71.10%；项目支出1,367.76万元，占28.90%。"
    table = [
        ["项目", "", "", "", "本年支出合计", "基本支出", "项目支出"],
        ["类", "款", "项", "合计", "4,733.14", "3,365.38", "1,367.76"],
        ["201", "", "", "一般公共服务支出", "474.40", "400.00", "74.40"],
    ]
    doc = make_doc([text, "支出决算表"], [None, table])
    issues = R33220_Narrative3_T3().apply(doc)
    assert issues == [], [str(i.message) for i in issues]


def test_v33220_narrative_table_mismatch_hits():
    """反例：说明值在表内找不到 → warn。"""
    text = "三、支出决算情况说明\n本年支出合计4,733.14万元，其中基本支出3,300.00万元，占70.00%；项目支出1,433.14万元，占30.00%。"
    table = [
        ["项目", "", "", "", "本年支出合计", "基本支出", "项目支出"],
        ["类", "款", "项", "合计", "4,733.14", "3,365.38", "1,367.76"],
        ["201", "", "", "一般公共服务支出", "474.40", "400.00", "74.40"],
    ]
    doc = make_doc([text, "支出决算表"], [None, table])
    issues = R33220_Narrative3_T3().apply(doc)
    assert any("基本支出不一致" in str(i.message) for i in issues)


# ---------------------------------------------------------------------------
# V33-244 三公表文一致（软换行断字 + 同比变化句）
# ---------------------------------------------------------------------------


def _three_public_narration():
    """复刻样张 P26 的软换行断字形态：「决\n算」被拆开。"""
    return [
        "七、财政拨款“三公”经费支出决算情况说明",
        "（一）“三公”经费财政拨款支出决算总体情况说明。",
        "“三公”经费财政拨款支出年初预算为21.00 万元，支出决算",
        "为16.95 万元，完成预算的80.71%，其中：因公出国（境）费决",
        "算为0.00 万元，完成预算的0.00%；公务用车购置及运行维护费",
        "支出决算为16.95 万元，完成预算的82.68%；公务接待费支出决",
        "算为0.00 万元，完成预算的0.00%。2025 年度“三公”经费支出",
        "决算数比2024 年度增加",
        "5.07 万元，增长42.68%，其中：因公出国（境）费支出决算为",
        "0.00 万元，与2024 年持平；公务用车购置及运行维护费支出决",
        "算增加5.07 万元，增长42.68%；公务接待费支出决算减少为0.00",
        "万元，与2024 年持平。",
    ]


def _three_public_table():
    """标准表七 12 列双口径布局：
    [合计B, 合计F, 出国B, 出国F, 用车小B, 用车小F, 购置B, 购置F, 运行B, 运行F, 接待B, 接待F]"""
    return [
        ["合计", "预算数", "决算数", "预算数", "决算数", "预算数", "决算数", "预算数", "决算数", "预算数", "决算数", "预算数"],
        ["21.00", "16.95", "0.00", "0.00", "17.00", "16.95", "0.00", "0.00", "17.00", "16.95", "4.00", "0.00"],
    ]


def test_v33244_softbroken_narration_consistent_with_table():
    """反例（软换行+同比句）：说明与表一致时不得因「决\\n算」断字把
    合计/公车决算数错配给出国、接待分项（样张 2 条假 error 根因）。"""
    doc = _three_public_doc(_three_public_narration())
    issues = R33244_Table7_ThreePublicAdvancedCheck().apply(doc)
    errors = [i for i in issues if i.severity == "error"]
    assert not any("表文不符" in str(i.message) for i in errors), [
        str(i.message) for i in errors
    ]


def _three_public_doc(narration_lines):
    """三公合成文档：说明文本两页 + 表页（含表名供 anchor 匹配）。"""
    pages = [
        "\n".join(narration_lines[:6]),
        "\n".join(narration_lines[6:]) + "\n财政拨款“三公”经费支出决算表",
    ]
    return make_doc(pages, [None, _three_public_table()])


def test_v33244_real_narration_table_mismatch_hits():
    """正例：说明与表真实不符 → error。

    行拼接后成句「其中：因公出国（境）费决算为2.00 万元」——
    与表内出国决算 0.00 矛盾，必须命中。
    """
    narration = list(_three_public_narration())
    narration[4] = "算为2.00 万元，完成预算的9.52%；公务用车购置及运行维护费"
    doc = _three_public_doc(narration)
    issues = R33244_Table7_ThreePublicAdvancedCheck().apply(doc)
    assert any("因公出国" in str(i.message) for i in issues)


# ---------------------------------------------------------------------------
# V33-245 / V33-246 新规则
# ---------------------------------------------------------------------------


def test_v33245_direction_flat_contradiction_hits():
    """正例（T5）：同主体「减少为0.00万元，与2024年持平」矛盾 → 命中。"""
    text = (
        "七、财政拨款“三公”经费支出决算情况说明\n"
        "公务接待费支出决算减少为0.00 万元，与2024 年持平。\n"
    )
    doc = make_doc([text], [[]])
    issues = R33245_ThreePublicDirectionContradiction().apply(doc)
    assert any(i.severity == "medium" for i in issues)


def test_v33245_direction_only_no_hit():
    """反例：只有增减表述、无持平表述 → 不命中。"""
    text = (
        "七、财政拨款“三公”经费支出决算情况说明\n"
        "公务用车购置及运行维护费支出决算增加5.07 万元，增长42.68%。\n"
    )
    doc = make_doc([text], [[]])
    assert R33245_ThreePublicDirectionContradiction().apply(doc) == []


def test_v33246_missing_domestic_reception_hits():
    """正例（T6）：仅披露外宾批次人次、缺国内批次 → 命中。"""
    text = (
        "3、公务接待费支出0 万元。其中：\n"
        "国内公务接待支出0.00 万（含外宾接待支出0.00 万元）。其\n"
        "中：接待外宾0 批次、0 人次。\n"
    )
    doc = make_doc([text], [[]])
    issues = R33246_DomesticReceptionDisclosure().apply(doc)
    assert any(i.severity == "medium" for i in issues)


def test_v33246_domestic_disclosed_no_hit():
    """反例：国内公务接待批次、人次已披露 → 不命中。"""
    text = (
        "3、公务接待费支出0 万元。其中：国内公务接待0 批次、0 人次。\n"
        "接待外宾0 批次、0 人次。\n"
    )
    doc = make_doc([text], [[]])
    assert R33246_DomesticReceptionDisclosure().apply(doc) == []


# ---------------------------------------------------------------------------
# detect_table_code：FIN_05 特异性 tie-break（识别缺口修复）
# ---------------------------------------------------------------------------


def test_detect_table_code_specific_alias_wins_over_generic():
    """正例：标题「一般公共预算财政拨款支出决算表」必须识别为 FIN_05，
    不得被先注册的泛别名规则 FIN_03（支出决算表）抢走。"""
    headers = ["合计", "基本支出", "项目支出"]
    code, confidence = detect_table_code(
        title="一般公共预算财政拨款支出决算表",
        headers=headers,
        source_hint="合计 基本支出 项目支出",
    )
    assert code == "FIN_05_general_public_expenditure"


def test_detect_table_code_generic_title_still_fin03():
    """反例：短标题「支出决算表」仍应识别为 FIN_03（tie-break 不反向误伤）。"""
    headers = ["合计", "基本支出", "项目支出"]
    code, confidence = detect_table_code(
        title="支出决算表",
        headers=headers,
        source_hint="合计 基本支出 项目支出",
    )
    assert code == "FIN_03_expenditure"


def test_detect_table_code_basic_expenditure_six():
    """正例：「一般公共预算财政拨款基本支出决算表」→ FIN_06。"""
    code, _ = detect_table_code(
        title="一般公共预算财政拨款基本支出决算表",
        headers=["经济分类科目编码", "科目名称", "决算数"],
        source_hint="经济分类科目编码 科目名称 决算数",
    )
    assert code == "FIN_06_basic_expenditure"


# ---------------------------------------------------------------------------
# GPT5.6 R6 P1-3：章节 scope 择优 + 跨章节隔离锁定
# ---------------------------------------------------------------------------


def test_v33_245_scope_selects_body_instance_not_toc():
    """find_section_scope 择优实例：目录同名标题（scope≈0）自动落选。"""
    from src.utils.narration import find_section_scope

    text = (
        "目录部分出现同名标题行\n"
        "七、财政拨款“三公”经费支出决算情况说明\n"  # 目录实例（下一行紧跟其他标题）
        "八、政府性基金预算财政拨款收入支出决算情况说明\n"
        "正文从这里开始\n"
        "七、财政拨款“三公”经费支出决算情况说明\n"  # 正文实例
        "（一）“三公”经费财政拨款支出决算总体情况说明。\n"
        "公务接待费支出决算减少为 0.00 万元，与2024年持平。\n"
    )
    scope = find_section_scope(text, ["三公"])
    assert scope and "公务接待费" in scope, (
        "目录实例被选中导致 scope 缺正文——R6 P1-3 回归"
    )


def test_v33_245_evidence_carries_section_tag():
    """V33-245 finding 的 evidence 自带结构化章节标记（R6 P1-3）。"""
    from src.engine.rules_v33 import R33245_ThreePublicDirectionContradiction

    text = (
        "七、财政拨款“三公”经费支出决算情况说明\n"
        "（一）“三公”经费财政拨款支出决算总体情况说明。\n"
        "公务接待费支出决算减少为 0.00 万元，与2024年持平。\n"
    )
    doc = make_doc([text], [])
    issues = R33245_ThreePublicDirectionContradiction().apply(doc)
    assert issues, "三公章节内矛盾应命中"
    evidence = issues[0].evidence_text or ""
    assert evidence.startswith("【章节:"), (
        f"evidence 应带章节标记前缀: {evidence[:40]!r}"
    )


def test_v33_245_cross_section_isolation_with_real_toc():
    """完整材料（目录+正文）中其他章节的公务接待表述不再产出 finding。

    GPT5.6 R6 实测：样张目录先出现「三公」标题 → scope 空 → or merged
    退回全文。R6 修复后 scope 择优正文实例，跨章节隔离真正生效。
    """
    from src.engine.rules_v33 import R33245_ThreePublicDirectionContradiction

    text = (
        "七、财政拨款“三公”经费支出决算情况说明\n"  # 目录实例
        "八、政府性基金预算财政拨款收入支出决算情况说明\n"
        "三、支出决算情况说明\n"
        "项目支出中列支的公务接待费支出决算减少为 0.00 万元，与上年持平。\n"
        "七、财政拨款“三公”经费支出决算情况说明\n"  # 正文实例
        "（一）“三公”经费财政拨款支出决算总体情况说明。\n"
        "公务用车运行维护费支出决算 16.95 万元，与上年基本持平。\n"
    )
    doc = make_doc([text], [])
    issues = R33245_ThreePublicDirectionContradiction().apply(doc)
    # 三公章节内只有「持平」没有矛盾主体增减对——0 finding；
    # 其他章节的矛盾表述（章节外）不得触发
    assert issues == [], f"跨章节隔离失效: {[i.message for i in issues]}"
