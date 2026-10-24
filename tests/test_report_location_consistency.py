from copy import deepcopy

from src.schemas.issues import AnalysisConfig, JobContext
from src.services.ai_findings import AIFindingsService
from src.services.ai_input_builder import _locate_page
from src.services.ai_page_display import synchronize_ai_page_display
from src.services.text_source_relations import add_text_source_relations

HASH = "a" * 64


def span_finding(id, source, pages, page, quote):
    start = pages[page - 1].index(quote)
    return {"id": id, "source": source, "rule_id": "V33-113" if source == "rule" else "AI-TEXT-000",
            "tags": ["duplicate_text"], "message": "重复用词：是是，疑似衍字。",
            "evidence": [{"physical_page": page, "file_hash": HASH, "quote": quote,
                          "source_span": [start, start + len(quote)], "location_level": "exact_span",
                          "verification_status": "verified"}]}


def test_common_occurrence_matches_exact_defect_across_different_context_spans():
    pages = ["封面", "原因是\n是根据工作安排开展；原因是是落实节约。"]
    rule = span_finding("r", "rule", pages, 2, "因是\n是根据工作")
    ai = span_finding("a", "ai", pages, 2, "原因是\n是根据工作安排开展")
    other = span_finding("other", "ai", pages, 2, "原因是是落实节约")
    result = {"rule_findings": [rule], "ai_findings": [ai, other]}
    before = deepcopy(result)
    add_text_source_relations(result, page_texts=pages, source_file_hash=HASH)
    relation = result["source_overlap_relations"][0]
    assert relation["ai_issue_ids"] == ["a"] and relation["rule_issue_ids"] == ["r"]
    assert relation["physical_page"] == 2 and relation["quote"] == "是是"
    assert result["rule_findings"] == before["rule_findings"]
    assert result["ai_findings"] == before["ai_findings"]


def test_same_word_elsewhere_wrong_hash_unverified_or_wrong_kind_does_not_match():
    pages = ["原因是是根据工作安排", "原因是是根据工作安排"]
    rule = span_finding("r", "rule", pages, 1, pages[0])
    ai = span_finding("a", "ai", pages, 2, pages[1])
    for mutation in ("page", "hash", "unverified", "kind", "offset"):
        candidate = deepcopy(ai)
        if mutation != "page":
            candidate = span_finding("a", "ai", pages, 1, pages[0])
        if mutation == "hash": candidate["evidence"][0]["file_hash"] = "b" * 64
        if mutation == "unverified": candidate["evidence"][0]["verification_status"] = "unverified"
        if mutation == "kind": candidate["tags"] = ["unit_scope_conflict"]
        if mutation == "offset": candidate["evidence"][0]["source_span"] = [1, 5]
        result = {"rule_findings": [rule], "ai_findings": [candidate]}
        add_text_source_relations(result, page_texts=pages, source_file_hash=HASH)
        assert result["source_overlap_relations"] == [], mutation


def test_old_generated_page_prefix_changes_but_quoted_page_is_preserved():
    old = "第22页存在重复表述。命中文本：请参阅第22页。"
    finding = {"source": "ai", "message": old, "page_number": 39,
               "source_binding_status": "bound", "verification_status": "verified"}
    synchronize_ai_page_display(finding)
    assert finding["message"] == "PDF 第 39 页存在重复表述。命中文本：请参阅第22页。"
    assert finding["location_original_message"] == old
    synchronize_ai_page_display(finding)
    assert finding["location_original_message"] == old


def test_unlocated_ai_does_not_assert_old_page_in_description():
    finding = {"source": "ai", "message": "第22页存在重复表述。", "page_number": 22,
               "source_binding_status": "unresolved"}
    synchronize_ai_page_display(finding)
    assert "第22页" not in finding["message"]
    assert finding["location_original_message"] == "第22页存在重复表述。"


def test_fabricated_span_cannot_override_explicit_page():
    pages = ["Cover", "Real evidence"]
    context = JobContext(job_id="page-test", pdf_path="test.pdf", page_texts=pages)
    service = AIFindingsService(AnalysisConfig())
    raw = {"problem_type": "duplicate_text", "confidence": .9, "quote": "Real evidence", "page": 2, "span": [0, 5]}
    item = service._convert_semantic_issues_to_items([raw], context, [(0, 5, 1), (6, 19, 2)])[0]
    assert item.page_number == 2
    assert "第2页" in item.message


def test_window_page_map_uses_physical_page_and_local_offsets():
    service = AIFindingsService(AnalysisConfig())
    service.page_offsets = [(0, 100, 1), (101, 130, 2), (131, 160, 3)]
    context = service._window_page_context(110, 30)
    assert "[0,20) = PDF 第 2 页" in context and "[21,30) = PDF 第 3 页" in context
    assert "PDF 第 1 页" not in context


def test_structured_context_never_guesses_first_page_on_absent_or_ambiguous_fact():
    assert _locate_page(["Cover", "Total 123"], "Total 123") == 2
    assert _locate_page(["Cover", "Total 123"], "Not in original") is None
    assert _locate_page(["Total 123", "Total 123"], "Total 123") is None
