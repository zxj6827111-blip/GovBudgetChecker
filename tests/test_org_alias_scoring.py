from src.services.org_matcher import get_org_matcher
from tests.test_upload_auto_match import _patch_runtime_state, _create_org


def test_equivalent_alias_outranks_city(tmp_path, monkeypatch):
    _patch_runtime_state(tmp_path, monkeypatch)
    _create_org("上海市", "city")
    unit = _create_org("上海市应用促进中心（上海市智慧城市建设促进中心）")
    matches = get_org_matcher().suggest_matches("generic.pdf", "上海市应用促进中心\n2025年度部门决算")
    assert matches[0][0].id == unit["id"]
    assert matches[0][1] == 0.96
