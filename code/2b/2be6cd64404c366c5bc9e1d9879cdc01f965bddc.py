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
