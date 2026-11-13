"""扫描续表的合成机制控制，不代替真实原件逐格验收。"""

from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace

import pytest

from tests.test_ocr_grid_reconstruction import fixture
from src.engine.fact_context import FactContext, resolve_operand
from src.services.fiscal_input_builder import FiscalInputBuilder


def continued(**second):
    first = fixture(title="公开02表 2025年部门收入决算表", page=1)
    tail = fixture(title=second.pop("title", "公开02表 2025年部门收入决算表"),
                   page=second.pop("page", 2), body_code=second.pop("body_code", "202"), **second)
    first.page_count = tail.page_count
    first.source_grids.extend(tail.source_grids)
    first.source_fragments.extend(tail.source_fragments)
    first.text_blocks.extend(tail.text_blocks)
    first.coordinate_transforms.update(tail.coordinate_transforms)
    return first


def test_confirmed_continuation_keeps_every_source_and_explicit_repeat_role():
    artifact = continued()
    original = deepcopy([g.to_dict() for g in artifact.source_grids])
    builder = FiscalInputBuilder()
    context = builder.facts(artifact)
    assert len(artifact.logical_tables) == 1
    assert len(context.facts) == 8  # 两页的每个实际金额格仍保留。
    assert len(context.definite_facts()) == 6
    assert {f.subject_code for f in context.facts} == {"201", "202", "total"}
    assert len({f.instance_uid for f in context.facts}) == 1
    refs = [f for f in context.facts if f.extra_dims.get("row_role") == "repeated_summary_reference"]
    assert len(refs) == 2 and all(f.recognition_status == "unverified" for f in refs)
    for fact in refs:
        parent = context.fact_by_uid(fact.extra_dims["summary_reference"]["canonical_fact_uid"])
        assert parent and parent.indicator == fact.indicator
        assert fact.physical_source_key() != parent.physical_source_key()
        assert parent.extra_dims["repeated_summary_claims"][0]["status"] == "consistent"
    result = resolve_operand(context, "total_actual", table_code="FIN_02_income")
    assert result.ok and result.value == Decimal("123.45")
    assert result.fact.extra_dims["evidence_refs"][0]["physical_page"] == 1
    assert [g.to_dict() for g in artifact.source_grids] == original
    assert builder.facts(artifact).to_dict() == context.to_dict()
    assert FactContext.from_dict(context.to_dict()).to_dict() == context.to_dict()
    ids = [c["logical_cell_id"] for c in artifact.logical_cell_candidates]
    assert len(ids) == len(set(ids))
    assert all(c["instance_uid"] == artifact.logical_tables[0].instance_uid for c in artifact.logical_cell_candidates)


@pytest.mark.parametrize("changes", [
    {"title": "公开03表 2025年部门收入决算表"},
    {"title": "公开02表 2024年部门收入决算表"},
    {"page": 3}, {"body_code": "201"}, {"extra_unit": "元"},
    {"title": "2025年部门收入决算表"},
])
def test_same_amount_is_never_sufficient_to_join(changes):
    artifact = continued(**changes)
    context = FiscalInputBuilder().facts(artifact)
    assert len(artifact.logical_tables) == 2
    assert not resolve_operand(context, "total_actual", table_code="FIN_02_income").ok
    assert not any(f.extra_dims.get("summary_reference") for f in context.facts)


def test_different_original_repeat_is_preserved_and_reported():
    from src.engine.rules_v33 import R33251_RepeatedSummaryClaims

    artifact = continued(amount="999.99")
    context = FiscalInputBuilder().facts(artifact)
    assert len(artifact.logical_tables) == 1 and len(context.facts) == 8
    assert {f.raw_value for f in context.facts if f.subject_code == "total"} == {"123.45", "999.99"}
    issues = R33251_RepeatedSummaryClaims().apply(SimpleNamespace(fact_context=context))
    assert len(issues) == 2
    assert all(i.location["pages"] == [1, 2] and len(i.fact_ids) == 2 for i in issues)
    assert {d["status"] for d in context.input_diagnostics} == {"repeated_summary_conflict"}
    from src.services.engine_rule_runner import EngineRuleRunner

    finding = EngineRuleRunner()._issue_to_finding(issues[0])
    assert [e["physical_page"] for e in finding.evidence] == [1, 2]
    assert {e["quote"] for e in finding.evidence} == {"123.45", "999.99"}
    assert all(e["verification_status"] == "unverified" for e in finding.evidence)


def test_business_projection_and_hierarchy_do_not_add_repeat_twice():
    from src.engine.fact_table_projection import fact_bound_table_rows
    from src.engine.native_hierarchy_checks import _table_view

    artifact = continued()
    context = FiscalInputBuilder().facts(artifact)
    doc = SimpleNamespace(parse_artifact=artifact, fact_context=context)
    rows = fact_bound_table_rows(doc, "收入决算表")
    assert sum("合计" in row for row in rows) == 1
    view, reason = _table_view(doc, "FIN_02_income")
    assert reason is None and set(view[4]) == {"total", "201", "202"}
    assert len(view[5]) == 6


def test_explicit_correction_refreshes_effective_repeat_comparison_without_mutating_raw():
    from src.engine.fact_context import FactOverlay, apply_fact_overlay
    from src.engine.rules_v33 import R33251_RepeatedSummaryClaims

    raw = FiscalInputBuilder().facts(continued(amount="999.99"))
    before = deepcopy(raw.to_dict())
    targets = [f for f in raw.facts if f.extra_dims.get("summary_reference")]
    effective = apply_fact_overlay(raw, [FactOverlay(f.fact_uid, Decimal("123.45")) for f in targets])
    assert raw.to_dict() == before
    assert R33251_RepeatedSummaryClaims().apply(SimpleNamespace(fact_context=raw))
    assert not R33251_RepeatedSummaryClaims().apply(SimpleNamespace(fact_context=effective))
    assert effective.version_token != raw.version_token
    assert all(f.extra_dims["evidence_refs"][0]["quote"] == "999.99"
               for f in effective.facts if f.extra_dims.get("summary_reference"))


def test_unread_adjacent_page_keeps_partial_table_out_of_complete_sum():
    artifact = continued()
    artifact.page_count = 3
    context = FiscalInputBuilder().facts(artifact)
    assert len(context.facts) == 8
    assert all(f.extra_dims["table_extent_status"] == "incomplete" for f in context.facts)
    assert not context.definite_facts()
    assert not resolve_operand(context, "total_actual", table_code="FIN_02_income").ok
    assert any(d.get("boundaries", {}).get("end", {}).get("physical_page") == 3
               for d in context.input_diagnostics)


@pytest.mark.parametrize("local,full,expected", [
    ("南朗街道人大办公室", "中山市南朗街道人大办公室", True),
    ("测试局", "广东省中山市测试局", True),
    ("人大办公室", "中山市南朗街道人大办公室", False),
    ("测试局", "测试局下属中心", False),
    ("办公室", "测试局办公室", False),
])
def test_source_organization_alias_requires_exact_administrative_prefix(local, full, expected):
    from src.services.ocr_grid_reconstruction import cover_organization_alias

    assert cover_organization_alias(local, full) is expected


def test_parenthesized_department_declaration_preserves_local_and_full_names():
    from src.services.ocr_grid_reconstruction import context_profile

    def group(text):
        return {"status": "resolved", "selected": {"text": text}}

    title = group("2025年收入决算表")
    profile = context_profile([title, group("部门（单位）：南朗街道人大办公室"), group("单位：万元")],
                              title, {}, {"org_name": "中山市南朗街道人大办公室", "report_year": 2025})
    assert profile["org_name"] == "中山市南朗街道人大办公室" and not profile["reasons"]
    assert profile["organization_alias"]["source_local_name"] == "南朗街道人大办公室"
    assert profile["sources"]["organization"][0]["text"].startswith("部门（单位）")



def test_bound_adjacent_title_is_a_boundary_even_when_its_amount_mapping_is_pending(monkeypatch):
    from src.services import ocr_grid_reconstruction as rebuild

    artifact = continued()
    end = fixture(title="公开03表 2025年部门支出决算表", page=3)
    artifact.page_count = 3
    artifact.source_grids.extend(end.source_grids)
    artifact.coordinate_transforms.update(end.coordinate_transforms)
    original = rebuild.semantic_mappings
    monkeypatch.setattr(rebuild, "semantic_mappings", lambda code, paths: [] if code == "FIN_03_expenditure"
                        else original(code, paths))
    context = FiscalInputBuilder().facts(artifact)
    assert len(context.definite_facts()) == 6
    extent = context.facts[0].extra_dims["table_extent_evidence"]
    proof = extent["boundaries"]["end"]
    assert extent["status"] == "confirmed" and proof["basis"] == "adjacent_bound_different_table_title"
    title = proof["source_titles"][0]["title_source"]["selected"]
    assert title["source"]["physical_page"] == 3 and title["source"]["raw_prediction_sha256"]


def test_unresolved_adjacent_title_cannot_close_partial_table(monkeypatch):
    from src.services import ocr_grid_reconstruction as rebuild

    artifact = continued()
    end = fixture(title="公开03表 2025年部门支出决算表", page=3,
                  low_context="公开03表 2025年部门支出决算表")
    artifact.page_count = 3
    artifact.source_grids.extend(end.source_grids)
    artifact.coordinate_transforms.update(end.coordinate_transforms)
    original = rebuild.semantic_mappings
    monkeypatch.setattr(rebuild, "semantic_mappings", lambda code, paths: [] if code == "FIN_03_expenditure"
                        else original(code, paths))
    context = FiscalInputBuilder().facts(artifact)
    assert len(context.facts) == 8 and not context.definite_facts()
    assert all(f.extra_dims["table_extent_status"] == "incomplete" for f in context.facts)



def test_headless_continuation_uses_real_prior_header_refs_without_new_page_header():
    first = fixture(title="公开02表 2025年部门收入决算表", page=1)
    tail = fixture(title="公开02表", page=2, body_code="202", headless=True)
    first.page_count = 2
    first.source_grids.extend(tail.source_grids)
    first.coordinate_transforms.update(tail.coordinate_transforms)
    physical = deepcopy([g.to_dict() for g in first.source_grids])
    context = FiscalInputBuilder().facts(first)
    assert len(first.logical_tables) == 1 and len(context.facts) == len(context.definite_facts()) == 8
    tail_facts = [f for f in context.facts if f.extra_dims["evidence_refs"][0]["physical_page"] == 2]
    assert len(tail_facts) == 4 and {f.subject_code for f in tail_facts} == {"202", "20201"}
    for fact in tail_facts:
        refs = fact.extra_dims["source_header_sources"]
        assert refs and {r["physical_page"] for r in refs} == {1}
        assert all(r["ocr_fragment_sources"] and r["source_relation"] == "shared_header_reference" for r in refs)
        assert fact.extra_dims["ocr_provenance"]["header_inheritance"]["version"].startswith("headless-continuation")
    table = first.reconstructed_tables[first.logical_tables[0].instance_uid]
    assert all(not c.is_header for c in table.cells if c.page_number == 2)
    assert [g.to_dict() for g in first.source_grids] == physical


@pytest.mark.parametrize("title,unit,code", [
    ("公开03表", None, "202"), ("公开02表", "元", "202"),
    ("公开02表", None, "201"), ("", None, "202"),
])
def test_headless_inheritance_requires_source_table_number_unit_and_code_boundary(title, unit, code):
    first = fixture(title="公开02表 2025年部门收入决算表", page=1)
    tail = fixture(title=title, page=2, body_code=code, headless=True, extra_unit=unit)
    first.page_count = 2
    first.source_grids.extend(tail.source_grids)
    first.coordinate_transforms.update(tail.coordinate_transforms)
    context = FiscalInputBuilder().facts(first)
    assert not any(f.extra_dims.get("ocr_provenance", {}).get("header_inheritance") for f in context.facts)
    assert not context.definite_facts()
