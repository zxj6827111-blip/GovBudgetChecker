from src.engine.pipeline import build_document
from src.engine.rules_v33 import R33234_NarrativePercentConsistency
from src.engine.native_narrative_quality import check_quality


def doc(body):
    return build_document("quality.pdf", ["甲局2025年度部门决算",
        "五、一般公共预算财政拨款支出决算情况说明\n" + body], [], 0)


def test_result_only_cause_is_a_wording_issue_with_direct_numeric_evidence():
    document = doc("年初预算为234430.42万元，支出决算为233564.77万元。"
                   "决算数小于预算数的主要原因是决算数与预算数基本持平。")
    issues = check_quality(R33234_NarrativePercentConsistency(), document)
    assert len(issues) == 1 and issues[0].severity == "medium"
    assert issues[0].location["relation_type"] == "reason_restates_result"
    assert "234430.42" in issues[0].location["table_refs"][0]["span"]


def test_actual_specific_reason_and_rounding_difference_are_not_flagged():
    document = doc("年初预算0万元，支出决算为150.17万元。"
                   "主要原因：发生死亡抚恤金据实支出。\n"
                   "年初预算100万元，支出决算为100.01万元。主要原因是决算数与预算数基本持平。")
    assert check_quality(R33234_NarrativePercentConsistency(), document) == []


def test_item_gap_is_not_reported_as_missing_subject_content():
    document = doc("1、甲\n2、乙\n3、丙\n5、丁")
    issues = check_quality(R33234_NarrativePercentConsistency(), document)
    assert len(issues) == 1 and issues[0].severity == "low"
    assert "不能据此判定科目内容缺失" in issues[0].message


def test_nested_number_reset_and_amount_decimal_do_not_count_as_missing_items():
    document = doc("1、甲\n2、乙\n1、子项甲\n2、子项乙\n3.25万元")
    assert check_quality(R33234_NarrativePercentConsistency(), document) == []
