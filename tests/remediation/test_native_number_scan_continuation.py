import hashlib
from copy import deepcopy

import pytest

from src.engine.fact_context import FactContext
from src.engine.pipeline import build_issues_payload
from src.engine.rules_v33 import R33004_CellNumberValidity
from src.services.evidence_guard import apply_evidence_completeness, count_formal_findings
from src.services.rule_source_binding import bind_rule_sources, verify_bound_rule_locations
from tests.remediation.test_native_blank_cells import grid_document


@pytest.mark.parametrize("gap_scope", ["one_cell", "unmatched_cell", "unlocated_region"])
def test_number_scan_rechecks_other_original_cells_on_the_same_page(tmp_path, gap_scope):
    import pymupdf

    rows = [
        ["Item", "Uncertain", "Percent", "Amount", "Normal", "Boundary"],
        ["A", "121%", "121%", "-2.50", "2.50", "100%"],
    ]
    doc = grid_document(rows, headers=1)
    artifact = doc.parse_artifact
    table = artifact.table_fragments[0]
    path = tmp_path / "source.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        for cell in table.cells:
            rect = pymupdf.Rect(cell.bbox)
            page.draw_rect(rect, color=(0, 0, 0))
            page.insert_text((rect.x0 + 7, rect.y0 + 15), cell.raw_text)
        doc.page_texts = [page.get_text()]
        pdf.save(path)
    doc.path = str(path)
    file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    artifact.source_file_hash = file_hash
    doc.fact_context = FactContext(facts=[], file_hash=file_hash, parse_run_uid=artifact.parse_run_uid,
                                   report_kind="final")
    # 旧页表抽取和当前原格证据不一致时，受保护页只用有来源的原格。
    doc.page_tables = [[[["999%"]]]]
    bad = next(c for c in table.cells if (c.row_idx, c.col_idx) == (1, 1))
    cell_id = "fragment:r1c1"
    receipt = {
        "mapping_incomplete_tables": [{
            "fragment_uid": table.fragment_uid, "table_code": table.table_code, "page": 1,
            "unresolved_money_cell_count": 1, "reasons": {"rule_admission_blocked": 1},
        }],
        "reading_quality": {"unbound_money_cells": [{
            "fragment_uid": table.fragment_uid, "table_code": table.table_code, "page": 1,
            "source_cell_id": cell_id if gap_scope != "unmatched_cell" else "fragment:r1c9",
            "raw_text": bad.raw_text, "bbox": list(bad.bbox), "reason": "rule_admission_blocked",
        }], "gaps": ([{"page": 1, "reason": "native_amount_not_extracted", "bbox": [0, 50, 24, 61]}]
                     if gap_scope == "unlocated_region" else [])},
    }
    doc._table_admission_receipt = (
        (file_hash, artifact.parse_run_uid, doc.fact_context.version_token), receipt)
    payload = build_issues_payload(doc, report_kind="final", rules=[R33004_CellNumberValidity])
    pending, = payload["rule_execution_summary"]["unresolved_rules"]
    assert pending["status"] == "insufficient_data"
    issues = payload["issues"]["all"]
    if gap_scope != "one_cell":
        assert not issues
        return

    assert {i["location"]["source_cell_id"] for i in issues} == {"fragment:r1c2", "fragment:r1c3"}
    result = {"rule_findings": issues}
    bind_rule_sources(result, page_texts=doc.page_texts, source_file_hash=file_hash,
                      artifact=artifact, fact_context=doc.fact_context)
    verify_bound_rule_locations(result, source_pdf_path=path)
    apply_evidence_completeness(result, document_page_count=1, page_texts=doc.page_texts,
                                source_file_hash=file_hash, source_pdf_path=path)
    assert count_formal_findings(result) == 2
    for issue in issues:
        evidence, = issue["evidence"]
        assert evidence["location_level"] == "exact_cell"
        assert evidence["source_cell_id"] == issue["location"]["source_cell_id"]
        assert evidence["bbox"][0] > bad.bbox[0]


@pytest.mark.parametrize("percent, expected", [("121%", 1), ("100%", 0)])
def test_unknown_table_region_does_not_block_an_independent_grid_on_that_page(tmp_path, percent, expected):
    import pymupdf

    doc = grid_document([["Item", "Share"], ["A", "131%"]], headers=1)
    artifact = doc.parse_artifact
    conflicted = artifact.table_fragments[0]
    independent = deepcopy(conflicted)
    independent.fragment_uid = "independent"
    independent.table_code = "UNKNOWN_P1_T1"
    independent.rows[1][1] = percent
    independent.bbox = tuple(value + (115 if axis % 2 else 0) for axis, value in enumerate(conflicted.bbox))
    for cell in independent.cells:
        cell.bbox = tuple(value + (115 if axis % 2 else 0) for axis, value in enumerate(cell.bbox))
        if (cell.row_idx, cell.col_idx) == (1, 1):
            cell.raw_text = percent
    artifact.table_fragments.append(independent)
    artifact.physical_fragments.append(deepcopy(independent))
    path = tmp_path / "two-grids.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        for table in (conflicted, independent):
            for cell in table.cells:
                rect = pymupdf.Rect(cell.bbox)
                page.draw_rect(rect)
                page.insert_text((rect.x0 + 7, rect.y0 + 15), cell.raw_text)
        doc.page_texts = [page.get_text()]
        pdf.save(path)
    doc.path = str(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    artifact.source_file_hash = digest
    doc.fact_context = FactContext(facts=[], file_hash=digest, parse_run_uid=artifact.parse_run_uid,
                                   report_kind="final")
    doc.page_tables = [[conflicted.rows, independent.rows]]
    doc._table_admission_receipt = (
        (digest, artifact.parse_run_uid, doc.fact_context.version_token),
        {"mapping_incomplete_tables": [{
            "table_code": conflicted.table_code, "page": 1, "fragment_uid": conflicted.fragment_uid,
            "reasons": {"amount_column_unmapped": 1}, "unresolved_money_cell_count": 1,
        }]},
    )
    payload = build_issues_payload(doc, report_kind="final", rules=[R33004_CellNumberValidity])
    assert payload["rule_execution_summary"]["rule_statuses"]["V33-004"] == "insufficient_data"
    bind_rule_sources(payload, page_texts=doc.page_texts, source_file_hash=digest, artifact=artifact)
    verify_bound_rule_locations(payload, source_pdf_path=str(path))
    apply_evidence_completeness(payload, document_page_count=1, page_texts=doc.page_texts,
                                source_file_hash=digest, source_pdf_path=str(path))
    assert count_formal_findings(payload) == expected
    assert all(issue["location"]["source_cell_id"] == "independent:r1c1"
               and issue["verification_status"] == "verified" for issue in payload["issues"]["all"])
