"""真实原页跨横线尾数：公开解析/财政事实入口与独立原字符证明。"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pdfplumber
import pytest

from src.engine.fact_context import build_fact_context_from_artifact, operand_rejection_reason
from src.schemas.parse_artifact import ParseArtifact
from src.services.pdf_parser import PDFParser
from src.services.table_import_receipt import build_table_import_receipt, source_cell_id

FIXTURES = Path(__file__).parents[1] / "fixtures/native_tail_v25"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text())


@pytest.fixture(scope="module")
def parsed():
    path = FIXTURES / "water_fin05.pdf"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == MANIFEST["fixture_sha256"]
    artifact = PDFParser(None).build_parse_artifact(str(path))
    context = build_fact_context_from_artifact(artifact, report_kind="final")
    assert context
    return artifact, context


@pytest.mark.parametrize("expected", MANIFEST["expected"], ids=lambda e: e["truth_id"])
def test_cross_grid_amount_keeps_independent_source_characters_and_physical_identity(parsed, expected):
    artifact, context = parsed
    identity = expected["expected"]
    matches = [f for f in context.facts if f.subject_code == identity["subject_code"]
               and "".join((f.subject_name or "").split()) == identity["subject_name"]
               and f.indicator == identity["indicator"]]
    assert len(matches) == 1
    fact = matches[0]
    assert fact.raw_value == expected["raw_text"]
    # 内容流只能证明字符存在；可见尾位跨格时必须保留歧义，不能据此放行取数。
    assert fact.recognition_status == "ambiguous"
    assert fact.extra_dims["qualification_evidence"]["source_conflict"] is True
    assert operand_rejection_reason(fact) is not None
    ref = fact.extra_dims["evidence_refs"][0]
    assert ref["physical_page"] == expected["page"]
    proof, = ref["source_provenance"]["native_text_sources"]
    independent = expected["source_proof"]
    assert (proof["start_char_index"], proof["end_char_index"], proof["text"]) == (
        independent["start_char_index"], independent["end_char_index"], independent["text"])
    original = {source_cell_id(t, c): c for t in artifact.source_grids for c in t.cells}[ref["source_cell_id"]]
    physical, = [t for t in artifact.physical_fragments if t.fragment_uid == ref["source_cell_id"].split(":")[0]]
    assert physical.page_number == expected["page"]
    assert original.raw_text == ref["source_provenance"]["grid_raw_text"]
    assert list(original.bbox) == proof["source_grid_bbox"]
    assert proof["source_grid_row"] == original.row_idx and proof["source_grid_col"] == original.col_idx
    with pdfplumber.open(FIXTURES / "water_fin05.pdf") as pdf:
        chars = pdf.pages[expected["page"] - 1].chars[proof["start_char_index"]:proof["end_char_index"]]
    assert "".join(c["text"] for c in chars) == expected["raw_text"]
    assert proof["bbox"] == [min(c["x0"] for c in chars), min(c["top"] for c in chars),
                             max(c["x1"] for c in chars), max(c["bottom"] for c in chars)]


def test_unadjudicated_cross_grid_candidates_keep_both_channels_and_stay_pending(parsed):
    artifact, context = parsed
    assert len(context.facts) == 156
    pending=[f for f in context.facts if f.recognition_status=='ambiguous']
    assert len(pending)==11
    assert all(operand_rejection_reason(f) is not None for f in pending)
    assert all(f.extra_dims['qualification_evidence']['source_conflict'] for f in pending)
    cells = {source: c for c in artifact.resolved_cells for source in c.source_cell_ids}
    for fact in pending:
        cell = cells[fact.extra_dims['evidence_refs'][0]['source_cell_id']]
        assert cell.resolution_status == 'unresolved'
        assert any(p.get('requires_adjudication') is True for p in cell.provenance)
    receipt = build_table_import_receipt(artifact, context.facts)
    assert receipt["amount_cell_count"] == 156 and receipt['rule_admitted_count']==145
    assert receipt["unresolved_money_cell_count"] == 11
    assert receipt["original_pdf_full_coverage_verified"] is False
    assert ParseArtifact.from_dict(artifact.to_dict()).to_dict() == artifact.to_dict()
    # 持久化真的经过JSON；嵌套证明也必须稳定，不能只做内存中的from_dict。
    assert ParseArtifact.from_dict(json.loads(json.dumps(artifact.to_dict()))).to_dict() == artifact.to_dict()
    # 总计尾字原来形成整行的整数格；它的归属证明不能被静默丢弃。
    tails = [c for t in artifact.physical_fragments for c in t.cells if any(
        p.get("recovery_kind") == "decimal_tail_owned_by_other_cell" for p in c.native_text_sources)]
    assert tails and all(c.grid_raw_text and c.raw_text == "" and c.numeric_value is None for c in tails)


@pytest.mark.parametrize("fault", ["tail_not_right_aligned", "native_order_interrupted", "label_not_on_head_baseline"])
def test_public_entrypoint_does_not_admit_an_unsafe_decimal_tail(fault):
    # 从公开的已打开PDF入口注入不可可靠解释的原字符，原PDF字节不变。
    with pdfplumber.open(FIXTURES / "water_fin05.pdf") as pdf:
        page = pdf.pages[0]
        if fault == "tail_not_right_aligned":
            for index in (691, 728, 771, 803, 839, 987):
                page.chars[index]["x0"] -= 4
                page.chars[index]["x1"] -= 4
        elif fault == "native_order_interrupted":
            page.chars.insert(691, {**page.chars[690], "text": " "})
        else:
            for char in page.chars:
                if char["x1"] < 298 and 509 <= char["top"] <= 520:
                    char["top"] -= 8
                    char["bottom"] -= 8
        artifact = PDFParser(None).build_parse_artifact_from_handle(pdf, file_hash=MANIFEST["fixture_sha256"])
    context = build_fact_context_from_artifact(artifact, report_kind="final")
    assert context
    row = [f for f in context.facts if f.subject_code == "212" and f.indicator == "total_actual"]
    assert not row or operand_rejection_reason(row[0]) is not None
    receipt = build_table_import_receipt(artifact, context.facts)
    assert receipt["reading_quality"]["gaps"] or receipt["unresolved_money_cell_count"] > 0


def test_character_identity_distinguishes_equal_amounts_on_neighboring_rows(parsed):
    _, context = parsed
    facts = [f for f in context.facts if f.raw_value == "121,914.65" and f.indicator == "total_actual"]
    assert {f.subject_code for f in facts} == {"212", "21203", "2120399"}
    assert len({f.extra_dims["evidence_refs"][0]["source_provenance"]["native_text_sources"][0]["start_char_index"]
                for f in facts}) == 3
    altered = deepcopy(facts[0])
    altered.raw_value = facts[1].raw_value
    assert altered.extra_dims["evidence_refs"][0]["source_cell_id"] != facts[1].extra_dims["evidence_refs"][0]["source_cell_id"]
