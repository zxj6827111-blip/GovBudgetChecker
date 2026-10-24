import pytest

from src.services.org_name_identity import compare_org_names, organization_name


@pytest.mark.parametrize(
    "a,b,expected",
    [
        ("甲局（本级）", "甲局本部", True),
        ("甲局（本级）", "甲局", False),
        ("甲局机关", "甲局（本级）", False),
        ("中国（上海）服务中心", "中国服务中心", False),
        ("甲市容（林业）管理站", "甲市容管理站", False),
        ("甲局服务中心", "甲局", False),
        ("甲 中心（２）", "甲中心(2)", True),
        ("甲单位（筹）", "甲单位", False),
    ],
)
def test_literal_identity(a, b, expected):
    assert compare_org_names(a, b).equivalent is expected


def test_alias_requires_unique_directory_owner():
    directory = [{"name": "甲发展中心（甲服务中心）"}]
    assert compare_org_names("甲发展中心", directory[0]["name"], directory).equivalent
    directory.append({"name": "甲发展中心"})
    assert not compare_org_names("甲发展中心", directory[0]["name"], directory).equivalent


def test_keywords_cannot_erase_identity_markers():
    directory = [{"name": "甲局（本级）", "keywords": ["甲局"]}]
    assert not compare_org_names("甲局", "甲局（本级）", directory).equivalent


def test_region_prefix_requires_unique_owner():
    directory = [{"name": "上海市甲中心"}]
    assert compare_org_names("上海甲中心", "上海市甲中心", directory).equivalent
    directory.append({"name": "上海甲中心"})
    assert not compare_org_names("上海甲中心", "上海市甲中心", directory).equivalent


def test_candidate_contract():
    assert organization_name({"organization_name": "甲局"}) == "甲局"
    with pytest.raises(ValueError, match="contract conflict"):
        organization_name({"name": "甲局", "organization_name": "乙局"})
