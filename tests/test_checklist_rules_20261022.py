"""检查点清单全系统默认规则（2026-10-22）：CMM-002 扩展、CMM-008~026、BUD-116~123、V33-251/252。

用例文字与金额取自《普陀区财政预算/决算报告检查点清单 v1.0》“常见错误表现”
原文，表格为合成 CONTRACT FIXTURE。每条规则至少一正一反。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List, Optional, Sequence

import pytest

from src.engine.checklist_budget_rules import (
    BUD116_FiscalTotalTableChecks,
    BUD117_IncomeSourceSum,
    BUD118_FunctionalHierarchySum,
    BUD119_CrossTableSubjectAmount,
    BUD120_MainContentItemization,
    BUD121_ProcurementBudget,
    BUD122_PerformanceElements,
    BUD123_ExplanationElements,
)
from src.engine.checklist_content_rules import (
    CMM020_ShareSum,
    CMM021_TableTitleNormative,
    CMM022_SubjectCodeFormat,
    CMM023_SectionTemplate,
    CMM024_CompositionSum,
    CMM025_PerformanceZeroCount,
)
from src.engine.checklist_final_rules import CMM026_EmptyNoteOnDataTable, R33251_FunctionalDetailCompleteness
from src.engine.checklist_identity_rules import (
    CMM017_OrgNameConsistency,
    CMM018_ScopeTerminology,
    CMM019_CoverElements,
)
from src.engine.checklist_structure_rules import CMM013_TocHeadingAlignment, CMM014_HeadingNumbering
from src.engine.checklist_support import unique_quote
from src.engine.checklist_text_rules import (
    CMM008_TemplateResidue,
    CMM009_TypoLexicon,
    CMM010_AmountFormat,
    CMM011_AmountUnitWan,
    CMM012_YearExpression,
)
from src.engine.common_rules import CMM002_TextAnomalyRule, CMM005_ComparativeNarrativeLogic
from src.engine.rule_outcome import RuleOutcomeSignal
from src.engine.rules_v33 import build_document


def _doc(pages: Sequence[str], kind: str = "budget", tables: Optional[List[Any]] = None,
         path: str = "/tmp/sample.pdf", org: Optional[str] = None, year: Optional[int] = None):
    doc = build_document(path, list(pages), tables if tables is not None else [[] for _ in pages], 1000)
    doc.report_kind = kind
    if org or year:
        field = lambda value: SimpleNamespace(value=value)  # noqa: E731
        doc.document_profile = SimpleNamespace(
            organization_name=field(org), report_year=field(year), subject_level=field(None),
            jurisdiction=field(None),
        )
    return doc


def _run(rule, doc) -> List[Any]:
    try:
        return rule.apply(doc) or []
    except RuleOutcomeSignal as signal:
        return list(getattr(signal, "partial_issues", []) or [])


def _messages(issues) -> str:
    return "\n".join(i.message for i in issues)


# ---- 引文唯一性 -------------------------------------------------------------


def test_unique_quote_expands_until_unique_on_page() -> None:
    page = "主要用于：主要用于：办公支出。\n主要用于：人员经费。"
    quote = unique_quote(page, 0, 5)
    assert page.count(quote) == 1 and quote.startswith("主要用于")


# ---- CMM-002 扩展：重复与标点（D-02/D-04） ----------------------------------


@pytest.mark.parametrize("text, expected", [
    ("下降的主要原因是主要原因是严格控制会议费预算，合理安排支出。", "主要原因是"),
    ("垃圾分类长效管理等支出支出，合计12.00万元。", "支出"),
    ("主要用于：主要用于：日常办公。", "主要用于"),
    ("与2024年度相比，与2024年度相比，支出增加。", "与2024年度相比"),
    ("主要用于于人员经费支出。", "于"),
])
def test_repetition_is_reported(text: str, expected: str) -> None:
    issues = CMM002_TextAnomalyRule().apply(_doc(["封面", text]))
    assert any(f"“{expected}”" in i.message for i in issues), _messages(issues)


def test_cross_word_overlap_and_topic_restatement_are_not_repetition() -> None:
    doc = _doc(["封面", "监测预测预警价格变动。", "本单位未安排政府性基金预算，政府性基金预算支出情况见下表。"])
    assert not any("重复" in i.message for i in CMM002_TextAnomalyRule().apply(doc))


def test_budget_punctuation_and_brackets() -> None:
    doc = _doc(["封面", "依据沪委组〔2019126号文件执行。", "收入包括：：一般公共预算。",
                "单价100万元（含）以上设备（不含车辆0台（套）。\n下一段正文。"])
    text = _messages(CMM002_TextAnomalyRule().apply(doc))
    assert "：：" in text and "括号未闭合" in text and "文号格式" in text


# ---- CMM-005 方向矛盾词（B-09/D-07） ----------------------------------------


def test_contradictory_direction_words() -> None:
    doc = _doc(["封面", "财政拨款收入支出增加减少的主要原因是人员经费的减少。", "比2024年度减增加0.01万元。"])
    text = _messages(CMM005_ComparativeNarrativeLogic().apply(doc))
    assert "增加减少" in text and "减增加" in text


# ---- CMM-008 模板残留（D-05/D-06） ------------------------------------------


def test_template_residue_variants() -> None:
    doc = _doc(["封面", "支出决算数比2023年度减少0.15万元，下降（增长）27.78%。",
                "比2024年预算增加（减少）0万元（持平）。",
                "五、国有资产占有使用情况（增加内容）",
                "截至2025年12月31日，本部门（单位）共有车辆0辆，特种专业技术用车X0辆。",
                "绩效目标设置情况（绩效目标管理工作情况，以及编报绩效目标的项目数量、单位数量、预算金额等）"],
               kind="final")
    text = _messages(CMM008_TemplateResidue().apply(doc))
    for token in ("下降（增长）", "（持平）", "（增加内容）", "部门（单位）", "X0", "绩效目标管理工作情况"):
        assert token in text, token


def test_generic_department_unit_phrase_is_not_residue() -> None:
    doc = _doc(["封面", "审核批复部门（单位）的年度预决算，及时拨付预算资金。"])
    assert CMM008_TemplateResidue().apply(doc) == []


# ---- CMM-009 错别字与漏字（D-01/D-03/D-08） ---------------------------------


@pytest.mark.parametrize("text, token", [
    ("主要用于咨询付、印刷费。", "咨询付"),
    ("用于津贴放发工作。", "放发"),
    ("科学技术支出（类）科技技术普及（款）", "科技技术"),
    ("公务车购置及运行费0万元。", "公务用车"),
    ("事业收0万元，其他收入0万元。", "漏“入”字"),
    ("一般公共预算拨支出预算2,731.38万元。", "预算拨款"),
    ("其他行政事业养老支出（项）7.19万元。", "单位"),
    ("主要用于无，列明公务用车购置数量。", "主要用于无"),
])
def test_typo_lexicon(text: str, token: str) -> None:
    issues = CMM009_TypoLexicon().apply(_doc(["封面", text]))
    assert any(token in i.message for i in issues), _messages(issues)


def test_typo_lexicon_joins_soft_wrapped_words() -> None:
    doc = _doc(["封面", "卫生健康支出（类）行政事业单位医疗（款）其他行\n政事业单位医疗（项）5.93万元。"])
    assert CMM009_TypoLexicon().apply(doc) == []


# ---- CMM-010 金额格式（C-15） -----------------------------------------------


@pytest.mark.parametrize("text, label", [
    ("增加67.797.80万元。", "句点"),
    ("事业收入 6，817.29 万元，占0.32%。", "全角逗号"),
    ("支出389.40.万元。", "多一个点号"),
    ("增加 25..67 万元。", "双小数点"),
    ("基本支出79,85.49 万元。", "千分位错位"),
    ("采购金额为 1,8415.60 万元。", "千分位错位"),
])
def test_amount_format(text: str, label: str) -> None:
    issues = CMM010_AmountFormat().apply(_doc(["封面", text]))
    assert any(label in i.message for i in issues), _messages(issues)


def test_amount_decimals_are_aggregated_once() -> None:
    doc = _doc(["封面", "收入138.981756万元，支出105.785712万元，基本工资42.9984万元。", "增长11.347%，减少3.12%。"])
    issues = CMM010_AmountFormat().apply(doc)
    assert sum("小数位不统一" in i.message and "万元" in i.message for i in issues) == 1
    assert any("百分比小数位" in i.message for i in issues)


# ---- CMM-011 万元单位（C-14） -----------------------------------------------


def test_missing_wan_unit() -> None:
    doc = _doc(["封面", "本年收入合计824.66元，一般公共预算拨款支出3686.46元。", "年初预算为 0 元，支出决算为12.90万元。",
                "差旅费标准为每人每天300元。"])
    issues = CMM011_AmountUnitWan().apply(doc)
    assert sum("漏写“万”字" in i.message for i in issues) == 2


# ---- CMM-012 年度（B-05） ---------------------------------------------------


def test_year_expressions_for_budget() -> None:
    doc = _doc(["上海市普陀区2026年区级部门预算", "比2026年预算执行数增加1.08%。", "20265年预算安排147.61万元。",
                "2025年本部门政府采购预算10.00万元。", "比2025年预算增加2.00万元。"], year=2026, org="上海市普陀区测试局")
    text = _messages(CMM012_YearExpression().apply(doc))
    assert "比较年度" in text and "20265" in text and "2025年本部门政府采购预算" in text
    assert "比2025年预算" not in text


def test_year_expressions_for_final() -> None:
    doc = _doc(["封面", "2024年度无国有资本经营预算财政拨款收入。", "与2025年度相比增加10.00万元。",
                "支出决算比2025年年初预算增加。"], kind="final", year=2025, org="上海市普陀区测试局")
    text = _messages(CMM012_YearExpression().apply(doc))
    assert "2024年度无国有资本经营预算" in text and "与2025年度" in text and "年初预算" not in text


# ---- CMM-013 目录与正文（A-05） ---------------------------------------------


def _final_toc_doc(body_second: str):
    toc = ("目 录\n第一部分 上海市普陀区测试局概况\n一、主要职能\n二、部门决算单位构成\n"
           "第三部分 上海市普陀区测试局 2025 年度部门决算情况说明\n一、收入支出决算总体情况说明\n"
           "二、收入决算情况说明\n三、支出决算情况说明")
    body1 = "第一部分 上海市普陀区测试局概况\n一、主要职能\n负责全区测试工作。\n" + body_second
    body3 = ("第三部分 上海市普陀区测试局 2025 年度部门决算情况说明\n一、收入支出决算总体情况说明\n收支总计1.00万元。\n"
             "二、收入决算情况说明\n收入1.00万元。\n三、支出决算情况说明\n支出1.00万元。")
    return _doc(["上海市普陀区测试局\n2025 年度部门决算", toc, body1, body3], kind="final", org="上海市普陀区测试局")


def test_toc_title_mismatch_is_reported() -> None:
    issues = CMM013_TocHeadingAlignment().apply(_final_toc_doc("二、机构设置\n本部门设3个科室。"))
    assert any("部门决算单位构成" in i.message and "机构设置" in i.message for i in issues)


def test_toc_aligned_document_is_clean() -> None:
    assert CMM013_TocHeadingAlignment().apply(_final_toc_doc("二、部门决算单位构成\n本部门含本级。")) == []


# ---- CMM-014 编号（E-04/E-05） ----------------------------------------------


def test_numbering_jump_duplicate_and_style() -> None:
    doc = _doc(["封面", "1.一般公共服务支出10.00万元。\n2.教育支出5.00万元。\n4.卫生健康支出1.00万元。\n"
                "5.住房保障支出1.00万元。\n5.其他支出1.00万元。\n6、节能环保支出1.00万元。"])
    text = _messages(CMM014_HeadingNumbering().apply(doc))
    assert "跳号" in text and "重号" in text and "体例不统一" in text


def test_numbering_marker_written_inline_is_not_a_jump() -> None:
    doc = _doc(["封面", "包括：（一）综合办公室；（二）总编室；\n（三）采访部；（四）编辑部；（五）运营部；\n（六）技术部。"])
    assert CMM014_HeadingNumbering().apply(doc) == []


# ---- CMM-017/018/019 主体身份 -----------------------------------------------


def test_org_name_variant() -> None:
    doc = _doc(["上海市普陀区规划和自然资源局\n2024 年度部门决算",
                "第一部分 上海市普陀区规划和自然资源局概况",
                "截至2024年12月31日，上海市规划和自然资源局共有车辆0辆。",
                "第二部分 上海市普陀区规划和自然资源局2024年度部门决算表"], kind="final",
               org="上海市普陀区规划和自然资源局")
    issues = CMM017_OrgNameConsistency().apply(doc)
    assert any("上海市规划和自然资源局" in i.message and "漏字" in i.message for i in issues)


def test_scope_terms_mixed_in_department_final() -> None:
    doc = _doc(["上海市普陀区测试局\n2025 年度部门决算", "二、部门决算单位构成\n第二部分 测试局2025年度部门决算表",
                "本单位建立了如下预算绩效管理制度。"], kind="final")
    issues = CMM018_ScopeTerminology().apply(doc)
    assert any("本单位" in i.message for i in issues)


def test_cover_missing_department_word() -> None:
    doc = _doc(["上海市普陀区国防动员办公室\n2025 年度决算",
                "目 录\n二、部门决算单位构成\n第二部分 上海市普陀区国防动员办公室 2025 年度部门决算表",
                "第三部分 上海市普陀区国防动员办公室 2025 年度部门决算情况说明"], kind="final",
               org="上海市普陀区国防动员办公室")
    issues = CMM019_CoverElements().apply(doc)
    assert any("漏写“部门”" in i.message for i in issues)


def test_cover_year_and_district() -> None:
    doc = _doc(["上海市2025年区级部门预算\n预算主管部门：上海市普陀区人力资源和社会保障局",
                "2026年部门财务收支预算总表\n2026年部门收入预算总表", "四、部门预算编制说明"],
               org="上海市普陀区人力资源和社会保障局")
    doc.document_profile.jurisdiction = SimpleNamespace(value="上海市普陀区")
    text = _messages(CMM019_CoverElements().apply(doc))
    assert "封面年度有误" in text and "漏写区名" in text


# ---- CMM-020 占比合计 / CMM-024 构成加总 / CMM-025 绩效 ----------------------


def test_share_sum_below_hundred() -> None:
    doc = _doc(["封面", "一般公共预算财政拨款支出10827.93万元，主要用于以下方面：一般公共服务支出（类）8960.41万元，占82.75%；"
                "社会保障和就业支出（类）266.92万元，占2.47%；节能环保支出（类）1315.22万元，占12.15%。"], kind="final")
    issues = CMM020_ShareSum().apply(doc)
    assert any("97.37%" in i.message for i in issues)


def test_composition_sum() -> None:
    doc = _doc(["封面", "本年收入合计19241.23万元，其中：财政拨款收入19121.23万元，占99.38%；其他收入20.00万元。",
                "本年支出合计100.00万元，其中：基本支出60.00万元，项目支出40.00万元。"], kind="final")
    issues = CMM024_CompositionSum().apply(doc)
    assert len(issues) == 1 and "收入构成" in issues[0].message


def test_performance_zero_count_with_amount() -> None:
    doc = _doc(["封面", "编报绩效目标的2025年度项目0个，涉及预算金额7.00万元；绩效自评的2025年度项目0个，涉及预算金额0万元。"],
               kind="final")
    issues = CMM025_PerformanceZeroCount().apply(doc)
    assert len(issues) == 1 and "7.00" in issues[0].message


# ---- CMM-021 表名 / CMM-022 编码 / CMM-023 章节 / CMM-026 空表说明 -----------


def test_final_table_title_extra_words() -> None:
    page = "一般公共预算财政拨款“三公”经费支出决算表\n单位：万元"
    doc = _doc(["封面", page], kind="final", tables=[[], [[["合计", "1.00"]]]])
    issues = CMM021_TableTitleNormative().apply(doc)
    assert issues and "财政拨款三公经费支出决算表" in issues[0].message


def test_subject_code_leading_zero_and_economic_class() -> None:
    page = "205 2 2 小学教育 100.00\n310 13 住房公积金 20.00\n"
    rows = [["205", "2", "2", "小学教育", "100.00"], ["310", "13", "住房公积金", "20.00"]]
    doc = _doc(["封面", page], tables=[[], [rows]])
    text = _messages(CMM022_SubjectCodeFormat().apply(doc))
    assert "205 02 02" in text and "301" in text


def test_unit_budget_must_not_have_project_section_and_department_must() -> None:
    base = ["上海市普陀区测试中心\n2026年区级单位预算\n预算单位：上海市普陀区测试中心", "目 录\n一、主要职能",
            "主要职能 机构设置 名词解释 政府采购 绩效目标 国有资产占有使用情况", "2026年单位财务收支预算总表\n四、单位预算编制说明"]
    unit = _doc(base + ["项目经费情况说明"])
    assert any("单位预算不应包含" in i.message for i in CMM023_SectionTemplate().apply(unit))
    dept = _doc([base[0].replace("单位", "部门"), base[1], base[2], "2026年部门财务收支预算总表\n四、部门预算编制说明"])
    assert any("项目经费情况说明" in i.message for i in CMM023_SectionTemplate().apply(dept))


def test_data_table_with_empty_note() -> None:
    page = "单位“三公”经费和机关运行经费预算表\n单位：万元\n合计 3.00\n注：本单位2026年无财政拨款三公经费预算安排，故本表为空表。"
    doc = _doc(["封面", page], tables=[[], [[["合计", "3.00"]]]])
    assert CMM026_EmptyNoteOnDataTable().apply(doc)


# ---- 预算表格勾稽 BUD-116~119 ------------------------------------------------

_T4_PAGE = "2026年部门财政拨款收支预算总表\n编制部门：上海市普陀区测试局 单位：元\n收入总计 19,658,967.00 支出总计 19,058,967.00"
_T4_ROWS = [
    ["财政拨款收入", None, "财政拨款支出", None, None, None, None],
    ["项目", "预算数", "项目", "合计", "一般公共预算", "政府性基金预算", "国有资本经营预算"],
    ["一、一般公共预算资金", "19658967.00", "一、一般公共服务支出", "19058967.00", "19058967.00", "", ""],
    ["收入总计", "19658967.00", "支出总计", "19058967.00", "19058967.00", "", ""],
]
_T2_PAGE = ("2026年部门收入预算总表\n编制部门：上海市普陀区测试局 单位：元\n"
            "201 一般公共服务支出 9,000,000.00 9,000,000.00\n201 39 社会工作事务 9,000,000.00 9,000,000.00\n"
            "201 39 01 行政运行 5,000,000.00 5,000,000.00\n201 39 99 其他社会工作事务支出 3,000,000.00 3,000,000.00\n"
            "合计 9,000,000.00 10,000,000.00")
_T2_ROWS = [
    ["项目", None, None, None, "收入预算", None, None],
    ["功能分类科目编码", None, None, "功能分类科目名称", "合计", "财政拨款收入", "其他收入"],
    ["类", "款", "项", None, None, None, None],
    ["201", "", "", "一般公共服务支出", "9000000.00", "9000000.00", ""],
    ["201", "39", "", "社会工作事务", "9000000.00", "9000000.00", ""],
    ["201", "39", "01", "行政运行", "5000000.00", "5000000.00", ""],
    ["201", "39", "99", "其他社会工作事务支出", "3000000.00", "3000000.00", ""],
    ["合计", None, None, None, "9000000.00", "10000000.00", ""],
]


def test_t4_balance_mismatch() -> None:
    doc = _doc(["封面", _T4_PAGE], tables=[[], [_T4_ROWS]])
    issues = _run(BUD116_FiscalTotalTableChecks(), doc)
    assert any("收支不平衡" in i.message for i in issues), _messages(issues)


def test_t2_row_sum_and_hierarchy() -> None:
    doc = _doc(["封面", _T2_PAGE], tables=[[], [_T2_ROWS]])
    row_text = _messages(_run(BUD117_IncomeSourceSum(), doc))
    assert "合计行" in row_text
    hierarchy = _messages(_run(BUD118_FunctionalHierarchySum(), doc))
    # 款“社会工作事务”900 万 ≠ 项 500 万 + 300 万
    assert "社会工作事务" in hierarchy and "下级科目之和800.00万元" in hierarchy


def test_t2_t3_same_subject_mismatch() -> None:
    t3_page = ("2026年部门支出预算总表\n编制部门：上海市普陀区测试局 单位：元\n201 一般公共服务支出 950.00 950.00\n合计 950.00 950.00")
    t3_rows = [["项目", None, None, None, "支出预算", None, None],
               ["功能分类科目编码", None, None, "功能分类科目名称", "合计", "基本支出", "项目支出"],
               ["类", "款", "项", None, None, None, None],
               ["201", "", "", "一般公共服务支出", "9500000.00", "9500000.00", ""],
               ["合计", None, None, None, "9500000.00", "9500000.00", ""]]
    t2_rows = [r[:] for r in _T2_ROWS]
    t2_rows[3] = ["201", "", "", "一般公共服务支出", "9000000.00", "9000000.00", ""]
    doc = _doc(["封面", _T2_PAGE, t3_page], tables=[[], [t2_rows], [t3_rows]])
    issues = _run(BUD119_CrossTableSubjectAmount(), doc)
    assert any("一般公共服务支出" in i.message and "两表不一致" in i.message for i in issues), _messages(issues)


# ---- 预算说明 BUD-120~123 ----------------------------------------------------


def test_main_content_class_level_and_sum() -> None:
    page = ("四、部门预算编制说明\n支出预算100.00万元，其中：财政拨款支出预算100.00万元。\n财政拨款支出主要内容如下：\n"
            "1.“一般公共服务支出（类）”60.00万元，主要用于机关运行。\n"
            "2.“住房保障支出（类）住房改革支出（款）住房公积金（项）”10.00万元，主要用于公积金。\n五、部门预算表")
    issues = _run(BUD120_MainContentItemization(), _doc(["封面", page]))
    text = _messages(issues)
    assert "未细化到项" in text and "列示不全" in text


def test_procurement_budget_elements() -> None:
    page = ("三、政府采购预算情况\n2026年度本部门政府采购预算30.00万元，其中：政府采购货物预算10.00万元、政府采购服务预算20.00万元。"
            "面向中小企业预留政府采购项目预算金额8.00万元，其中，预留给小型和微型企业的政府采购项目预算为10.00万元。\n四、绩效目标设置情况")
    doc = _doc(["上海市普陀区测试局\n2026年区级部门预算", page, "2026年部门财务收支预算总表"])
    text = _messages(_run(BUD121_ProcurementBudget(), doc))
    assert "工程" in text and "小型和微型企业" in text


def test_performance_elements_and_project_seven_items() -> None:
    pages = ["上海市普陀区测试局\n2026年区级部门预算", "2026年部门财务收支预算总表",
             "四、绩效目标设置情况\n本部门开展了绩效目标编报工作，编报绩效目标的项目5个，涉及项目预算资金508.00万元。\n五、国有资产占有使用情况",
             "测试局项目经费情况说明\n一、项目概述\n二、立项依据\n四、实施方案\n六、年度预算安排\n七、绩效目标"]
    text = _messages(_run(BUD122_PerformanceElements(), _doc(pages)))
    assert "预算单位数" in text and "实施主体" in text and "实施周期" in text


def test_explanation_elements_missing_main_content() -> None:
    page = "四、部门预算编制说明\n2026年收入预算100.00万元，其中：财政拨款收入100.00万元。一般公共预算拨款支出预算100.00万元。\n其他相关情况说明"
    text = _messages(_run(BUD123_ExplanationElements(), _doc(["封面", page])))
    assert "财政拨款支出主要内容" in text


# ---- 决算 V33-251 -------------------------------------------------------------


def test_final_detail_sum_and_item_level() -> None:
    page = ("（三）一般公共预算财政拨款支出决算具体情况\n一般公共预算财政拨款支出年初预算为100.00万元，支出决算为90.00万元。其中：\n"
            "1、教育支出（类）普通教育（款）小学教育（项）。主要用于办学，年初预算60.00万元，支出决算为50.00万元。\n"
            "2、住房保障支出（类）。主要用于公积金，年初预算10.00万元，支出决算为10.00万元。\n"
            "六、一般公共预算财政拨款基本支出决算情况说明")
    issues = _run(R33251_FunctionalDetailCompleteness(), _doc(["封面", page], kind="final"))
    text = _messages(issues)
    assert "未细化到项" in text and "年初预算合计70.00万元" in text and "支出决算合计60.00万元" in text
