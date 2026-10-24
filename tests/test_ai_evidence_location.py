"""AI 定位必须从原件取得坐标，修正页码不能替 AI 确认业务结论。"""
from copy import deepcopy
import hashlib

import pdfplumber
import pytest

from src.services.ai_evidence_location import enrich_ai_evidence_locations
from src.services.rule_source_binding import bind_ai_literal_sources, verify_bound_ai_locations


def original(tmp_path, pages):
    import fitz
    path = tmp_path / "original.pdf"
    with fitz.open() as pdf:
        for lines in pages:
            page = pdf.new_page()
            for row, text in enumerate(lines):
                page.insert_text((72, 72 + row * 20), text, fontsize=12)
        pdf.save(path)
    with pdfplumber.open(path) as pdf:
        texts = [page.extract_text() or "" for page in pdf.pages]
    return path, texts, hashlib.sha256(path.read_bytes()).hexdigest()


def finding(quote, page=1, **extra):
    return dict(id="ai-test", source="ai", title="Amount", message="Review the amount",
                severity="manual_review", original_severity="high", page_number=page,
                evidence_status="degraded_missing_evidence", location={"page": page},
                evidence=[{"page": page, "quote": quote, "text": quote, **extra}])


def test_historical_wrong_page_is_repaired_and_original_snapshot_is_unchanged(tmp_path):
    path, pages, digest = original(tmp_path, [["Cover"], ["Housing expenditure 661.08, share 8.69%"]])
    item = finding("Housing expenditure 661.08, share 8.69%", context="AI abbreviated context...")
    payload = {"analysis_revision": 3, "result": {"ai_findings": [item]}}
    before = deepcopy(payload)
    result = enrich_ai_evidence_locations(payload, path)
    located = result["result"]["ai_findings"][0]
    assert payload == before
    assert located["page_number"] == 2 and located["claimed_page_number"] == 1
    assert located["bbox"] and located["location_level"] == "exact_span"
    assert located["evidence_verification"]["source_identity_complete"] is True
    assert located["evidence_verification"]["status"] == "verified"
    assert located["evidence_status"] == "complete"
    assert located["severity"] == "manual_review" and located["review_kind"] == "manual_review"
    assert located["evidence"][0]["file_hash"] == digest
    assert "context" not in located["evidence"][0]
    assert located["evidence_explanation"][0]["context"] == "AI abbreviated context..."
    # 缓存返回值不能被某次请求污染。
    located["bbox"][0] = 0
    assert enrich_ai_evidence_locations(payload, path)["result"]["ai_findings"][0]["bbox"][0] > 0


@pytest.mark.parametrize("quote,pages", [
    ("Invented expenditure 999.00", [["Original expenditure 123.45"]]),
    ("Repeated expenditure 123.45", [["Cover"], ["Repeated expenditure 123.45"], ["Repeated expenditure 123.45"]]),
    ("123.45", [["Cover"], ["Original expenditure 123.45"]]),
])
def test_absent_ambiguous_and_short_numeric_quotes_never_gain_a_box(tmp_path, quote, pages):
    path, texts, digest = original(tmp_path, pages)
    item = finding(quote)
    result = {"ai_findings": [item]}
    receipt = bind_ai_literal_sources(result, page_texts=texts, source_file_hash=digest)
    verify_bound_ai_locations(result, source_pdf_path=str(path))
    assert receipt["bound_ai_findings"] == 0
    assert item.get("bbox") is None
    assert item["source_binding_status"] == "unresolved"


def test_cross_page_quote_keeps_each_fragment_on_its_own_physical_page(tmp_path):
    path, texts, digest = original(tmp_path, [["Cover"], ["Expenditure was 123.45"], ["and the share was 8.69%"]])
    item = finding("Expenditure was 123.45 and the share was 8.69%")
    result = {"ai_findings": [item]}
    bind_ai_literal_sources(result, page_texts=texts, source_file_hash=digest)
    verify_bound_ai_locations(result, source_pdf_path=str(path))
    assert item["page_number"] == 2
    assert [ref["physical_page"] for ref in item["evidence"]] == [2, 3]
    assert all(ref.get("bbox") and ref["verification_status"] == "verified" for ref in item["evidence"])
    assert [ref["page"] for ref in item["location"]["table_refs"]] == [2, 3]


def test_pdf_replacement_invalidates_location_cache(tmp_path):
    path, _, _ = original(tmp_path, [["Cover"], ["Original expenditure 123.45"]])
    payload = {"result": {"ai_findings": [finding("Original expenditure 123.45")]}}
    assert enrich_ai_evidence_locations(payload, path)["result"]["ai_findings"][0]["bbox"]
    path.unlink()
    original(tmp_path, [["Different document"]])
    item = enrich_ai_evidence_locations(payload, path)["result"]["ai_findings"][0]
    assert item.get("bbox") is None
    assert item["evidence_status"] == "degraded_missing_evidence"


def test_pdf_replacement_revokes_already_located_geometry_and_nested_refs(tmp_path):
    path, _, _ = original(tmp_path, [["Cover"], ["Original expenditure 123.45"]])
    payload = {"result": {"ai_findings": [finding("Original expenditure 123.45")]}}
    located = enrich_ai_evidence_locations(payload, path)
    assert located["result"]["ai_findings"][0]["bbox"]
    path.unlink()
    original(tmp_path, [["Different document"]])
    repaired = enrich_ai_evidence_locations(located, path)["result"]["ai_findings"][0]
    assert repaired["bbox"] is None and repaired["page_number"] is None
    assert repaired["location"]["table_refs"] == []
    assert all(e.get("bbox") is None and e["verification_status"] != "verified" for e in repaired["evidence"])


@pytest.mark.parametrize("suffix", ["…", "..."])
def test_truncated_long_quote_binds_only_the_literal_retained_prefix(tmp_path, suffix):
    prefix = "Car expenditure 123.45, total 234.56 and share 52.64 percent"
    path, _, _ = original(tmp_path, [["Cover"], [prefix + ", with other expenses."]])
    payload = {"result": {"ai_findings": [finding(prefix + suffix)]}}
    item = enrich_ai_evidence_locations(payload, path)["result"]["ai_findings"][0]
    assert item["page_number"] == 2 and item["bbox"]
    assert item["evidence"][0]["quote"] == prefix
    assert item["evidence"][0]["quote_truncated"] is True
    assert item["evidence_explanation"][0]["quote"] == prefix + suffix
    assert item["severity"] == "manual_review"
