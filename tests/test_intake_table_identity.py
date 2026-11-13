import pytest

from src.engine.rules_v33 import build_document, R33002_NineTablesCheck
from src.engine.rule_outcome import RuleDeferred
from src.services.fiscal_table_rules import detect_table_code, is_unknown_table_code
from src.services.fiscal_fact_materializer import infer_fund_scope


@pytest.mark.parametrize("code,expected", [
    ("UNKNOWN_P1_T0", True),
    ("UNKNOWN_P25_T0", True),
    ("UNKNOWN", True),
    ("", True),
    ("FIN_02_income", False),
    ("FIN_07_three_public", False),
    ("UNKNOWNISH", False),
])
def test_unknown_table_code_accepts_the_parsers_real_encoding(code, expected):
    """解析器产出 UNKNOWN_P<页>_T<序号>；只认字面量 UNKNOWN 会漏掉真实编码。"""
    assert is_unknown_table_code(code) is expected


@pytest.mark.parametrize("title,headers,expected", [
    ("政拨款收入支出决算总表", ["一般公共预算", "政府性基金", "国有资本经营"], "FIN_04_fiscal_grant_total"),
    ("府性基金预算财政拨款收入支出决算表", ["年初结转和结余", "本年收入", "本年支出"], "FIN_08_gov_fund"),
    ("政拨款“三公”经费支出决算表", ["公务用车购置及运行费", "公务接待费"], "FIN_07_three_public"),
    ("一般公共预算财政拨款三公经费及机关运行经费支出决算表", ["公务用车", "公务接待费"], "FIN_07_three_public"),
])
def test_title_suffix_uses_independent_structure(title, headers, expected):
    assert detect_table_code(title, headers)[0] == expected


def test_truncated_title_without_headers_does_not_fall_back_to_short_table():
    assert detect_table_code("政拨款收入支出决算总表", [])[0] is None


def test_missing_text_defers_missing_table_claim():
    doc = build_document("scan.pdf", [""], [[]], 1)
    with pytest.raises(RuleDeferred, match="不能.*原件缺表"):
        R33002_NineTablesCheck().apply(doc)


def test_recognized_three_public_defines_appropriation_scope_even_with_truncated_title():
    assert infer_fund_scope("FIN_07_three_public", "政拨款三公经费支出决算表") == "fiscal_appropriation"


def test_annual_chapter_heading_does_not_replace_actual_table_title():
    from types import SimpleNamespace
    from src.engine.checklist_content_rules import CMM021_TableTitleNormative, table_title_lines
    text = '第二部分\n甲市测试中心2025年\n度决算表\n收入支出决算总表\n单位：万元'
    doc = build_document('2025决算.pdf', [text], [[[['项目', '金额'], ['合计', '1.00']]]], 0)
    doc.parse_artifact = SimpleNamespace(table_fragments=[SimpleNamespace(page_number=1, table_code='FIN_01_income_expenditure_total')])
    assert [row[-1] for row in table_title_lines(doc)] == ['收入支出决算总表']
    assert CMM021_TableTitleNormative().apply(doc) == []
