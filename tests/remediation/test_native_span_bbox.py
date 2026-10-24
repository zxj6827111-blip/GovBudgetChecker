"""Only authenticated native text spans can acquire original character bboxes."""

import hashlib
from pathlib import Path

import pdfplumber
import pytest

from src.schemas.issues import IssueItem
from src.services.rule_source_binding import _native_span
from src.utils.issue_bbox import PDFBBoxLocator


def source(tmp_path, pages, *, chinese=False):
    fitz = pytest.importorskip("fitz")
    path = tmp_path / "native-span.pdf"
    document = fitz.open()
    for lines in pages:
        page = document.new_page()
        for row, line in enumerate(lines):
            page.insert_text((72, 72 + row * 20), line, fontsize=12,
                             fontname="china-s" if chinese else "helv")
    document.save(path)
    document.close()
    with pdfplumber.open(path) as pdf:
        texts = [page.extract_text() or "" for page in pdf.pages]
    return path, texts, hashlib.sha256(path.read_bytes()).hexdigest()


def bind(quote, page, texts, digest):
    ref = _native_span(quote, page, texts, digest)
    assert ref is not None
    return ref


def rehash(ref):
    ref["evidence_id"] = hashlib.sha1("|".join((ref["file_hash"], str(ref["physical_page"]),
                                                ref["source_block_id"], ref["quote"])).encode()).hexdigest()[:16]
    return ref


def locate(path, refs):
    item = IssueItem(id="span", source="rule", severity="medium", title="Native claim",
                     message="Native claim", evidence=refs, location={"page": 1, "section": "claims",
                                                                      "pages": list(range(1, 1 + len(refs)))})
    locator = PDFBBoxLocator(str(path))
    try:
        result = locator.locate(item)
        if result.bbox:
            anchor = next(ref for ref in result.evidence if ref.get("bbox") == result.bbox
                          and ref.get("physical_page") == result.page_number)
            assert locator._region_text_matches(result.page_number, result.bbox, anchor["quote"])
        return result
    finally:
        locator.close()


def test_authenticated_multiline_thousands_comma_quote_gets_original_char_bbox(tmp_path):
    path, texts, digest = source(tmp_path, [["Car expenditure is 43.34,", "declared completion 95.26%, total 1,234.56."]])
    ref = bind("Car expenditure is 43.34, declared completion 95.26%, total 1,234.56.", 1, texts, digest)
    result = locate(path, [ref])
    assert result.location_level == "exact_span"
    assert result.verification_status == "verified"
    assert result.evidence[0]["source_span"] == ref["source_span"]
    assert result.evidence[0]["quote"] == ref["quote"]
    assert result.bbox[1] < 72 and result.bbox[3] > 90


def test_nfkc_input_is_bound_to_the_unchanged_original_quote_and_characters(tmp_path):
    path, texts, digest = source(tmp_path, [["金额１，２３４．５６万元"]], chinese=True)
    ref = bind("金额1,234.56万元", 1, texts, digest)
    assert ref["quote"] in texts[0]
    result = locate(path, [ref])
    assert result.location_level == "exact_span"
    assert result.evidence[0]["quote"] == ref["quote"]


@pytest.mark.parametrize("mutation", ["hash", "other_page", "quote", "offset", "unbound"])
def test_invalid_source_contract_cannot_promote_even_when_quote_exists(tmp_path, mutation):
    path, texts, digest = source(tmp_path, [["Car completion 95.26%", "Other completion 75.45%"],
                                         ["Car completion 95.26%", "Other completion 75.45%"]])
    ref = bind("Car completion 95.26%", 1, texts, digest)
    if mutation == "hash":
        ref["file_hash"] = "a" * 64
    elif mutation == "other_page":
        ref["page"] = ref["physical_page"] = 2  # Its native source block still belongs to page 1.
    elif mutation == "quote":
        ref["quote"] = ref["text"] = ref["text_snippet"] = "Other completion 75.45%"
    elif mutation == "offset":
        ref["source_span"] = [1, ref["source_span"][1] + 1]
        ref["source_block_id"] = f"native:p1:s1:e{ref['source_span'][1]}"
    else:
        ref["source_identity_status"] = "unverified"
    # A plausible pre-supplied rectangle cannot bypass the source-span gate.
    ref.update(bbox=[70, 55, 350, 105], location_level="exact_span", verification_status="verified")
    rehash(ref)
    result = locate(path, [ref])
    assert result.bbox is None
    assert result.location_level != "exact_span"
    assert result.evidence[0].get("bbox") is None
    assert result.evidence[0]["verification_status"] == "unverified"


def test_duplicate_short_quote_does_not_choose_the_first_character_location(tmp_path):
    path, texts, digest = source(tmp_path, [["Car completion 95.26%", "Overseas completion 95.26%"]])
    assert _native_span("95.26%", 1, texts, digest) is None
    start = texts[0].index("95.26%")
    ref = rehash({"page": 1, "physical_page": 1, "quote": "95.26%", "text": "95.26%",
                  "file_hash": digest, "source_block_id": f"native:p1:s{start}:e{start + 6}",
                  "source_span": [start, start + 6], "source_method": "native_text",
                  "source_identity_status": "verified", "verification_status": "unverified"})
    result = locate(path, [ref])
    assert result.bbox is None
    assert result.evidence[0]["verification_status"] == "unverified"


def test_literal_completion_claim_is_primary_and_table_operands_stay_on_their_page(tmp_path):
    fitz = pytest.importorskip("fitz")
    path, texts, digest = source(tmp_path, [["43.34 67.30"], ["Car completion is 95.26%"]])
    with fitz.open(path) as pdf:
        word = pdf[0].get_text("words")[0]
    cell = {"page": 1, "physical_page": 1, "quote": "43.34", "text": "43.34", "file_hash": digest,
            "source_cell_id": "table:r0c1", "source_identity_status": "verified",
            "location_level": "exact_cell", "verification_status": "verified", "bbox": list(word[:4])}
    cell["evidence_id"] = hashlib.sha1(f"{digest}|1|table:r0c1|43.34".encode()).hexdigest()[:16]
    claim = bind("Car completion is 95.26%", 2, texts, digest)
    claim["role"] = "完成率声明"
    result = locate(path, [cell, claim])
    assert (result.page_number, result.location_level, result.verification_status) == (2, "exact_span", "verified")
    assert result.evidence[0]["physical_page"] == 1
    assert result.evidence[0]["location_level"] == "exact_cell"
    assert result.evidence[0]["bbox"] == cell["bbox"]
    assert result.evidence[1]["physical_page"] == 2
    assert result.evidence[1]["location_level"] == "exact_span"
    assert result.bbox == result.evidence[1]["bbox"]


def test_span_and_nested_table_reference_use_the_same_geometry_gate(tmp_path):
    path, texts, digest = source(tmp_path, [["Procurement claim 1,234.56"]])
    ref = bind("Procurement claim 1,234.56", 1, texts, digest)
    item = IssueItem(id="procurement", source="rule", severity="medium", title="Claim", message="Claim",
                     evidence=[ref], location={"page": 1, "section": "Procurement", "table_refs": [ref]})
    locator = PDFBBoxLocator(str(path))
    try:
        result = locator.locate(item)
    finally:
        locator.close()
    assert result.evidence[0]["location_level"] == "exact_span"
    assert result.location["table_refs"][0]["location_level"] == "exact_span"
    assert result.location["table_refs"][0]["bbox"] == result.evidence[0]["bbox"]
