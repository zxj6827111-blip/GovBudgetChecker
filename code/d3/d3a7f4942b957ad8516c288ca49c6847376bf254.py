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
