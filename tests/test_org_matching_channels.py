from api.routes import upload
from tests.test_upload_auto_match import _patch_runtime_state, _create_org


def test_cover_parameters_do_not_pollute_original_page(tmp_path, monkeypatch):
    _patch_runtime_state(tmp_path, monkeypatch)
    _create_org("甲市财政局")
    captured = []

    class Matcher:
        def suggest_matches(self, filename, first_page_text, **kwargs):
            captured.append((filename, first_page_text, kwargs))
            return []

    monkeypatch.setattr(upload, "get_org_matcher", lambda: Matcher())
    text = "甲市财政局\n2025年度部门决算"
    upload._collect_org_matches(filename="other.pdf", first_page_text=text,
                                cover_org_name="甲市财政局", scope_hint="department")
    assert all(item[1] == text for item in captured)
    assert captured[0][2]["cover_org_name"] == "甲市财政局"
    assert captured[0][2]["scope_hint"] == "department"
