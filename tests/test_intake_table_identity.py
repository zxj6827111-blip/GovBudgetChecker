import pytest

from src.engine.rules_v33 import build_document, R33002_NineTablesCheck
from src.engine.rule_outcome import RuleDeferred
from src.services.fiscal_table_rules import detect_table_code
from src.services.fiscal_fact_materializer import infer_fund_scope


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
