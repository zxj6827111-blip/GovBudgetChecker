"""原件报告身份继承反例，全部为合成机制控制。"""

from copy import deepcopy

from test_ocr_grid_reconstruction import fixture
from src.services.fiscal_input_builder import FiscalInputBuilder
from src.services.fiscal_source_profile import source_document_profile


def cover(artifact, rows, page=1):
    for row, text in enumerate(rows):
        for index, char in enumerate(text):
            artifact.source_fragments.append(
                {
                    "granularity": "character",
                    "physical_page": page,
                    "char_index": row * 100 + index,
                    "text": char,
                    "bbox": [index * 12 + 20, row * 40 + 30, index * 12 + 30, row * 40 + 45],
                    "upright": True,
                }
            )
    return artifact


def test_cover_year_and_organization_have_actual_source_anchors_and_ignore_wrong_parameters():
    artifact = cover(fixture(title="部门收入决算表", page=4), ["测试局", "2024年度部门决算"])
    raw = deepcopy(artifact.source_fragments)
    context = FiscalInputBuilder().facts(
        artifact, org_name="错局", fiscal_year=2030, report_kind="budget"
    )
    assert len(context.definite_facts()) == 4
    assert {(f.org_name, f.fiscal_year, f.budget_stage) for f in context.facts} == {
        ("测试局", 2024, "决算")
    }
    assert {f.extra_dims["report_year"] for f in context.facts} == {2024}
    assert {f.extra_dims["amount_year"] for f in context.facts} == {2024}
    sources = context.facts[0].extra_dims["ocr_provenance"]["context_profile"]["sources"][
        "document"
    ]["sources"]
    assert (
        sources and sources[0]["sources"][0]["sources"][0]["file_hash"] == artifact.source_file_hash
    )
    assert artifact.source_fragments == raw


def test_explicit_table_amount_year_is_separate_from_report_year():
    artifact = cover(fixture(title="2023年部门收入决算表", page=4), ["测试局", "2024年度部门决算"])
    context = FiscalInputBuilder().facts(artifact)
    assert len(context.definite_facts()) == 4
    assert {(f.extra_dims["report_year"], f.extra_dims["amount_year"]) for f in context.facts} == {
        (2024, 2023)
    }
    from src.engine.fact_context import resolve_operand

    assert context.fiscal_year == 2024 and {f.fiscal_year for f in context.facts} == {2023}
    assert resolve_operand(context, "total_actual", report_year=2024).status == "blocked"
    assert resolve_operand(context, "total_actual", report_year=2023).ok
    assert not resolve_operand(context, "total_actual").ok
    assert resolve_operand(context, "total_actual", amount_year=2023).ok


def test_two_explicit_source_report_years_are_preserved_and_not_admitted():
    artifact = cover(fixture(title="部门收入决算表", page=4), ["测试局", "2024年度部门决算"])
    cover(artifact, ["测试局", "2023年度部门决算"], page=2)
    context = FiscalInputBuilder().facts(artifact)
    assert len(context.facts) == 4 and not context.definite_facts()
    evidence = source_document_profile(artifact)
    assert evidence["report_year"] is None
    assert {d["report_year"] for d in evidence["sources"]} == {2023, 2024}


def test_explicit_table_organization_conflict_does_not_disappear():
    artifact = cover(fixture(title="部门收入决算表", page=4), ["农业局", "2024年度部门决算"])
    context = FiscalInputBuilder().facts(artifact)
    assert len(context.facts) == 4 and not context.definite_facts()
    assert {f.org_name for f in context.facts} == {"测试局"}
    assert (
        "explicit_table_and_cover_organization_conflict"
        in context.facts[0].extra_dims["ocr_provenance"]["context_profile"]["reasons"]
    )


def test_later_chapter_cannot_supply_an_earlier_table_and_no_global_unit_is_inherited():
    artifact = fixture(title="部门收入决算表", page=1)
    cover(artifact, ["测试局", "2024年度部门决算", "单位：亿元"], page=2)
    assert not source_document_profile(artifact)["sources"]
    assert not FiscalInputBuilder().facts(artifact, fiscal_year=2024).definite_facts()
    artifact = cover(
        fixture(title="部门收入决算表", page=4), ["测试局", "2024年度部门决算", "单位：亿元"]
    )
    context = FiscalInputBuilder().facts(artifact)
    assert len(context.definite_facts()) == 4
    assert {f.unit for f in context.facts} == {"万元"}


def test_missing_source_does_not_fill_year_from_parameters():
    context = FiscalInputBuilder().facts(fixture(title="部门收入决算表"), fiscal_year=2024)
    assert len(context.facts) == 4 and not context.definite_facts()
    assert all(f.fiscal_year is None for f in context.facts)


def test_source_cover_year_and_stage_can_be_on_adjacent_native_lines():
    artifact = cover(fixture(title="部门收入决算表", page=4), ["测试局", "2024年度", "部门决算"])
    context = FiscalInputBuilder().facts(artifact)
    assert len(context.definite_facts()) == 4
    assert {f.extra_dims["report_year"] for f in context.facts} == {2024}


def test_source_ocr_cover_keeps_real_fragment_refs_without_requiring_a_table_grid():
    table = fixture(title="部门收入决算表", page=4)
    source = fixture(title="2024年度部门决算", page=1)
    for transform in source.coordinate_transforms.values():
        transform["page_render"] = {
            "original_parent_image_sha256": transform["tile_context"]["root_image_sha256"]
        }
    table.coordinate_transforms.update(source.coordinate_transforms)
    context = FiscalInputBuilder().facts(table)
    assert len(context.definite_facts()) == 4
    assert {f.extra_dims["report_year"] for f in context.facts} == {2024}
    profile = source_document_profile(table)
    refs = profile["sources"][0]["sources"][0]["sources"][0]["selected"]
    assert refs["source"]["physical_page"] == 1
    assert refs["granularity"] == "fragment" and "char_index" not in refs
    # 根图绑定失配时封面不能提供可靠报告身份，表格内容仍保留。
    for transform in source.coordinate_transforms.values():
        transform["page_render"]["original_parent_image_sha256"] = "f" * 64
    table.coordinate_transforms.update(source.coordinate_transforms)
    assert not source_document_profile(table)["sources"]
    assert not FiscalInputBuilder().facts(table, fiscal_year=2024).definite_facts()


def test_source_multiline_organization_is_complete_not_just_last_suffix():
    artifact = cover(
        fixture(title="部门收入决算表", page=4),
        ["上海市人民政府办公厅", "离退休干部服务中心", "2024年度部门决算"],
    )
    profile = source_document_profile(artifact)
    assert profile["org_name"] == "上海市人民政府办公厅离退休干部服务中心"
    assert len(profile["sources"][0]["sources"]) == 3


def test_cover_report_year_before_organization_is_preserved_even_when_unexpected():
    artifact = cover(
        fixture(title="2024年部门收入决算表", page=4),
        ["2025年度中山市南朗街道人大办公室", "决算公开"],
    )
    profile = source_document_profile(artifact)
    assert (profile["report_year"], profile["org_name"], profile["report_kind"]) == (
        2025,
        "中山市南朗街道人大办公室",
        "final",
    )
    # 表内主体不同，原文候选均保留，而不是按预期改掉封面年度。
    context = FiscalInputBuilder().facts(artifact)
    assert len(context.facts) == 4 and not context.definite_facts()
    assert {(f.extra_dims["report_year"], f.extra_dims["amount_year"]) for f in context.facts} == {
        (2025, 2024)
    }


import pytest


@pytest.mark.parametrize("names,expected,aliases,scopes", [
    (["上海市水务局", "（上海市海洋局）"], "上海市水务局", ["上海市海洋局"], []),
    (["上海市人民政府参事室"], "上海市人民政府参事室", [], []),
    (["上海市人民政府办公厅"], "上海市人民政府办公厅", [], []),
    (["中共上海市委金融委员会", "办公室"], "中共上海市委金融委员会办公室", [], []),
    (["上海市经济和信息化委员", "会"], "上海市经济和信息化委员会", [], []),
    (["上海市发展和改革委员会", "(本级)"], "上海市发展和改革委员会", [], ["本级"]),
])
def test_cover_name_block_keeps_alias_and_scope_separate(names, expected, aliases, scopes):
    artifact = cover(fixture(title="部门收入决算表", page=4), names + ["2024年度部门决算"])
    original = deepcopy(artifact.source_fragments)
    profile = source_document_profile(artifact)
    assert (profile["org_name"], profile["org_aliases"], profile["org_scopes"]) == (expected, aliases, scopes)
    assert len(profile["sources"][0]["sources"]) == len(names) + 1
    assert artifact.source_fragments == original


def test_display_profile_uses_same_context_and_keeps_rejected_call_and_filename():
    from src.services.document_profile_resolver import resolve_document_profile
    from src.services.fiscal_source_profile import bind_document_profile

    artifact = cover(fixture(title="部门收入决算表", page=4), ["测试局", "2024年度部门决算"])
    context = FiscalInputBuilder().facts(artifact, org_name="错局", fiscal_year=2030, report_kind="budget")
    wrong = resolve_document_profile(filename="2030年错局预算.pdf", explicit_report_kind="budget")
    bound = bind_document_profile(wrong, context)
    assert (bound.kind, bound.year, bound.organization_name.value) == ("final", 2024, "测试局")
    assert bound.report_kind.rejected[0]["value"] == "budget"
    assert bound.report_year.rejected[0]["value"] == 2030
    diagnostic = next(d for d in context.input_diagnostics if d.get("kind") == "source_document_profile")
    assert diagnostic["requested_mismatches"]["fiscal_year"] == {"requested":2030, "source":2024}
    assert (wrong.kind, wrong.year) == ("budget", 2030)



def test_native_materialization_ignores_wrong_owner_year_and_kind_before_build(tmp_path):
    import pymupdf
    from tests.remediation.test_native_structure_pdf_pipeline import text, lines

    path = tmp_path / "2030年错局预算.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=760, height=600)
        text(page, 100, 100, "测试局")
        text(page, 100, 145, "2024年度部门决算")
        page = pdf.new_page(width=760, height=600)
        text(page, 30, 50, "收入决算表")
        text(page, 30, 75, "单位：万元")
        xs, ys = [30, 130, 340, 540, 730], [100, 140, 185]
        lines(page, xs, ys)
        for col, value in enumerate(["科目编码", "科目名称", "本年收入合计", "财政拨款收入"]):
            text(page, xs[col]+5, 125, value)
        for col, value in enumerate(["201", "一般公共服务支出", "123.45", "123.45"]):
            text(page, xs[col]+5, 165, value)
        pdf.save(path)
    builder = FiscalInputBuilder()
    artifact = builder.read_native(path, org_name="错局", fiscal_year=2030, report_kind="budget")
    raw = deepcopy([fragment.to_dict() for fragment in artifact.physical_fragments])
    context = builder.facts(artifact)
    assert len(context.facts) == len(context.definite_facts()) == 2
    assert {(f.org_name, f.fiscal_year, f.budget_stage) for f in context.facts} == {("测试局", 2024, "决算")}
    assert {f.extra_dims["report_year"] for f in context.facts} == {2024}
    for fact in context.facts:
        refs = fact.extra_dims["source_header_sources"]
        assert refs and all(r["physical_page"] == 2 and r["file_hash"] == artifact.source_file_hash for r in refs)
        assert all(r["native_text_sources"] and r["source_relation"] == "shared_header_reference" for r in refs)
    assert [fragment.to_dict() for fragment in artifact.physical_fragments] == raw



def test_both_rule_entrypoints_route_the_bound_source_kind_instead_of_wrong_upload():
    from types import SimpleNamespace
    from src.engine.pipeline import _resolve_report_kind
    from src.services.engine_rule_runner import EngineRuleRunner
    from src.services.fiscal_source_profile import bind_rule_document

    context = FiscalInputBuilder().facts(cover(fixture(title="部门收入决算表", page=4),
                                             ["测试局", "2024年度部门决算"]))
    doc = SimpleNamespace(fact_context=context, path="2030年错局预算.pdf", page_texts=[""],
                          report_kind="budget", dominant_year=2030)
    job = SimpleNamespace(meta={"report_kind":"budget"}, pdf_path=doc.path)
    assert _resolve_report_kind(doc, "budget") == "final"
    assert EngineRuleRunner()._resolve_report_kind(job, doc) == "final"
    bind_rule_document(doc)
    assert (doc.report_kind, doc.dominant_year, doc.org_name) == ("final", 2024, "测试局")
    assert doc.document_profile.kind == "final" and doc.document_profile.year == 2024



def test_two_separate_geographic_owners_are_not_joined_into_an_invented_name():
    artifact = cover(fixture(title="部门收入决算表", page=4),
                     ["甲市人民政府研究室", "乙市人民政府参事室", "2024年度部门决算"])
    profile = source_document_profile(artifact)
    assert profile["org_name"] is None and "explicit_cover_org_name_conflict" in profile["reasons"]
    context = FiscalInputBuilder().facts(artifact, org_name="测试局", fiscal_year=2024)
    assert len(context.facts) == 4 and not context.definite_facts()
