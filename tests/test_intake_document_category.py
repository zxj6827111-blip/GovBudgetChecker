from api.runtime import extract_cover_metadata
from src.services.document_profile_resolver import detect_cover_facts, resolve_document_profile
from src.engine.rule_registry import select_rules


def test_performance_report_is_non_budget_even_with_budget_mentions():
    profile = resolve_document_profile(filename="2025年预算绩效.pdf", page_texts=["2025年度绩效自评报告\n项目预算金额100万元"])
    assert profile.kind == "non_budget"
    assert select_rules(profile.kind) == []


def test_mixed_material_keeps_both_candidates_and_does_not_pick_year():
    text = "甲市财政局\n2024年度部门决算及2025年度部门预算"
    profile = resolve_document_profile(page_texts=[text])
    assert profile.kind == "unknown"
    assert profile.year is None
    assert {c["year"] for c in profile.report_kind.rejected} == {2024, 2025}
    assert extract_cover_metadata(page_texts=[text])["report_year"] is None


def test_body_notice_is_not_cover_title():
    assert detect_cover_facts(["财政部关于下达2023年资金预算的通知》"]) ["cover_title"] == ""


def test_normal_final_body_budget_mentions_do_not_conflict():
    profile = resolve_document_profile(page_texts=["甲市财政局\n2025年度部门决算", "年初预算100万元"])
    assert profile.kind == "final"
