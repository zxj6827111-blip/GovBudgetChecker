import pytest

from src.services.org_binding_decision import decide_auto_binding
from src.services.org_name_identity import compare_org_names


def org(name="甲市财政局", level="department", id="d", parent_id=None):
    return dict(name=name, level=level, id=id, parent_id=parent_id)


def decide(pages, directory, **kwargs):
    return decide_auto_binding(filename="甲市财政局2025年度部门决算.pdf", page_texts=pages,
                               directory=directory, **kwargs)


def code(result):
    return result["reasons"][0]["code"]


def test_filename_only_never_binds_and_evidence_has_no_fake_page():
    result = decide([], [org()])
    assert code(result) == "no_original_evidence"
    assert result["reasons"][0]["evidence"][0]["page"] is None


def test_city_candidate_never_binds():
    assert decide(["上海市\n2025年度部门决算"], [org("上海市", "city")])["decision"] == "needs_confirmation"


def test_subordinate_cover_cannot_bind_parent():
    result = decide(["甲市财政局服务中心\n2025年度单位决算"], [org()])
    assert result["decision"] == "needs_confirmation"


def test_body_heading_can_bind_with_truncated_cover():
    result = decide(["甲市财政局服务中\n2025年度决算", "第一部分 甲市财政局服务中心概况"],
                    [org("甲市财政局服务中心", "unit", "u")])
    assert result["organization_id"] == "u"
    assert any(e["page"] == 2 for e in result["evidence"])


def test_department_requires_explicit_scope_and_benji_does_not_change_equivalence():
    directory = [org(), org("甲市财政局（本级）", "unit", "u", "d")]
    assert not compare_org_names("甲市财政局", "甲市财政局（本级）", directory).equivalent
    assert code(decide(["甲市财政局\n2025年度决算"], directory)) == "scope_undetermined"
    assert decide(["甲市财政局\n2025年度部门决算"], directory)["organization_id"] == "d"
    result = decide(["甲市财政局\n2025年度单位决算"], directory)
    assert result["organization_id"] == "u"
    assert result["match_basis"] == "combined_source_scope"


@pytest.mark.parametrize("directory,expected", [
    ([org()], "directory_missing_org"),
    ([org(), org("甲市财政局（本级）", "unit", "u")], "organization_relation_unverified"),
    ([org(), org("甲市财政局（本级）", "unit", "u", "d"), org("甲市财政局本部", "unit", "v", "d")], "equivalent_candidates_tie"),
])
def test_combined_path_missing_requirements(directory, expected):
    assert code(decide(["甲市财政局\n2025年度单位决算"], directory)) == expected


def test_scope_conflict_keeps_both_witnesses():
    result = decide(["甲市财政局（本级）\n2025年度部门决算"], [org(), org("甲市财政局（本级）", "unit", "u", "d")])
    assert code(result) == "evidence_conflict"
    assert {e.get("scope") for e in result["evidence"]} >= {"unit", "department"}


def test_actual_composition_outweighs_template_words_but_does_not_override_cover():
    pages = ["甲市财政局\n2025年度决算", "目录\n部门决算单位构成", "部门决算包括：本级决算、下属行政事业单位决算。"]
    assert decide(pages, [org()])["organization_id"] == "d"
    pages[0] = "甲市财政局\n2025年度单位决算"
    assert code(decide(pages, [org()])) == "evidence_conflict"


def test_serialized_candidate_field_is_supported():
    candidate = {"organization_name": "甲市财政局", "id": "d", "level": "department"}
    assert decide(["甲市财政局\n2025年度部门决算"], [candidate])["organization_id"] == "d"
