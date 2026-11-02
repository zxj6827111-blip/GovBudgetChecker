import pytest

from src.services.document_profile_resolver import detect_cover_facts, original_org_evidence


@pytest.mark.parametrize("name", [
    "中国（上海）自由贸易试验区管理委员会", "甲市绿化（林业）工程管理站",
    "甲市民防办公室", "甲市监狱管理局机关", "甲市水务局（甲市海洋局）", "甲市财政局（本级）",
])
def test_cover_preserves_identity_and_same_line_year(name):
    facts = detect_cover_facts([name + "2022\n年度决算"])
    assert facts["cover_org_name"].replace("（", "(").replace("）", ")") == name.replace("（", "(").replace("）", ")")


def test_closed_directory_accepts_non_suffix_name_only_on_cover():
    directory = [{"name": "甲市青少年营地——东方绿舟"}]
    assert detect_cover_facts(["甲市青少年营地——东方绿舟\n2025年度决算"], directory)["cover_org_name"] == directory[0]["name"]
    assert not detect_cover_facts(["2025年度决算", "甲市青少年营地——东方绿舟"], directory)["cover_org_name"]


def test_body_evidence_retains_physical_page():
    evidence = original_org_evidence(["2025年度决算", "第一部分 甲市服务中心概况"])
    assert evidence == [{"source": "part_heading", "page": 2, "line": 1, "text": "甲市服务中心"}]


@pytest.mark.parametrize("suffix", ["年度部门决算", "年度单位预算", "年度决算"])
def test_same_line_year_followed_by_complete_annual_document_title(suffix):
    facts = detect_cover_facts(["甲市民防办公室2022\n" + suffix])
    assert facts["cover_org_name"] == "甲市民防办公室"


def test_nested_formal_parentheses_and_attachment_marker_preserve_literal_name():
    facts = detect_cover_facts(["附件2\n甲市学院附属卫生\n学校（甲市护理职业学\n院（筹））\n2025年度决算"])
    assert facts["cover_org_name"] == "甲市学院附属卫生学校(甲市护理职业学院(筹))"
    assert not detect_cover_facts(["甲市卫生学校（甲市护理学院（筹）\n2025年度决算"])["cover_org_name"]
