"""Re-run native PDF extraction and real rules against original local files.

This is a development diagnostic, not a human accuracy benchmark. It preserves
the parse artifact and checks every fact's literal source cell after merging.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pdfplumber

from src.engine.check_obligations import build_obligation_ledger
from src.engine.fact_context import build_fact_context_from_artifact
from src.engine.fact_table_projection import TABLE_CODES, fact_bound_table_rows
from src.engine.pipeline import build_document, run_rules_with_outcomes
from src.engine.rule_outcome import summarize_rule_outcomes
from src.engine.native_empty_operands import valid_blank_observation
from src.services.document_profile_resolver import resolve_document_profile
from src.services.pdf_parser import PDFParser
from src.services.structure_view import build_structure_view


def native_dimension_source_is_valid(cell, source, native_chars, native_words):
    if source.get('basis') == 'native_open_bottom_row':
        matches = [w for w in native_words.get(cell.page_number, [])
            if w['text']==source['text'] and [w['x0'],w['top'],w['x1'],w['bottom']]==source['bbox']]
        return len(matches)==1 and bool(cell.bbox) and (
            cell.bbox[0] <= source['bbox'][0] <= source['bbox'][2] <= cell.bbox[2]
            and cell.bbox[1]-.5 <= source['bbox'][1] <= source['bbox'][3] <= cell.bbox[3]+.5)
    chars = native_chars.get(source.get('physical_page'), [])[source['start_char_index']:source['end_char_index']]
    literal = ''.join(c.get('text','') for c in chars)
    box = [min(c['x0']for c in chars),min(c['top']for c in chars),
           max(c['x1']for c in chars),max(c['bottom']for c in chars)] if chars else None
    if literal != source['text'] or box != source['bbox']:
        return False
    if cell.grid_raw_text is None:
        # 开口网格尾行原本没有物理格；不能要求它伪造一个旧网格字面值。
        # 新行必须完整回指字符，并落在独立恢复的该格位置内。
        return source.get('recovery_kind')=='open_grid_tail' and bool(cell.bbox) and (
            ''.join(s['text']for s in cell.native_text_sources)==cell.raw_text
            and cell.bbox[0]-.5 <= box[0] <= box[2] <= cell.bbox[2]+.5
            and cell.bbox[1]-.5 <= box[1] <= box[3] <= cell.bbox[3]+.5)
    return True


def audit(path: Path, output: Path) -> dict:
    started = time.monotonic()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    parser = PDFParser(None)
    with pdfplumber.open(path) as pdf:
        texts = [(page.extract_text() or "") for page in pdf.pages]
        artifact = parser.build_parse_artifact_from_handle(pdf, file_hash=before)
        native_chars = {number: page.chars for number, page in enumerate(pdf.pages, 1)}
        native_words = {number: page.extract_words() for number,page in enumerate(pdf.pages,1)}
    profile = resolve_document_profile(filename=path.name, page_texts=texts)
    context = build_fact_context_from_artifact(
        artifact, org_name=profile.organization_name.value,
        fiscal_year=profile.year, report_kind=profile.kind,
        doc_type="dept_final" if profile.kind == "final" else "dept_budget",
    )
    page_tables = [[] for _ in texts]
    for fragment in artifact.physical_fragments:
        if fragment.extraction_method != "page_text_fallback":
            page_tables[fragment.page_number - 1].append(fragment.rows)
    doc = build_document(str(path), texts, page_tables, path.stat().st_size)
    doc.parse_artifact = artifact
    doc.fact_context = context
    doc.document_profile = profile
    doc.structure_view = build_structure_view(texts, artifact)
    issues, outcomes = run_rules_with_outcomes(doc, report_kind=profile.kind)
    summary = summarize_rule_outcomes(outcomes)
    coverage = build_obligation_ledger(
        profile, report_kind=profile.kind, rule_execution_summary=summary,
        ai_execution={"requested": False, "state": "not_requested"},
    )
    receipts = getattr(doc, 'fact_receipts', [])
    observations = {json.dumps(item, ensure_ascii=False, sort_keys=True): item
                    for receipt in receipts for item in receipt.get('cell_observations', [])}
    blank_source_errors = [item for item in observations.values()
                           if not valid_blank_observation(artifact, context, item)]
    disclosure_sources = [item for receipt in receipts for item in receipt.get('disclosure_sources', [])]
    disclosure_source_errors = [item for item in disclosure_sources
                                if item.get('file_hash') != before
                                or not isinstance(item.get('page'), int)
                                or not 1 <= item['page'] <= len(texts)
                                or not item.get('span') or item['span'] not in texts[item['page'] - 1]]
    dimension_cells = [(table, cell) for table in artifact.physical_fragments
                       for cell in table.cells if cell.native_text_sources]
    dimension_source_errors = []
    for table, cell in dimension_cells:
        for source in cell.native_text_sources:
            if not native_dimension_source_is_valid(cell, source, native_chars, native_words):
                dimension_source_errors.append({'fragment_uid': table.fragment_uid, 'row': cell.row_idx,
                                                'col': cell.col_idx, 'source': source})
    physical_cells = {
        f"{fragment.fragment_uid}:r{cell.row_idx}c{cell.col_idx}": cell
        for fragment in artifact.physical_fragments for cell in fragment.cells
    }
    source_errors = []
    for fact in context.facts:
        for ref in fact.extra_dims.get("evidence_refs", []):
            source = physical_cells.get(ref.get("source_cell_id"))
            if (source is None or ref.get("quote") != source.raw_text.strip()
                    or ref.get("physical_page") != source.page_number
                    or ref.get("file_hash") != before
                    or list(ref.get("bbox") or []) != list(source.bbox or [])
                    or parser._parse_numeric(source.raw_text) is None
                    or fact.normalized_value != Decimal(str(parser._parse_numeric(source.raw_text))) *
                    {"元": Decimal("0.0001"), "万元": Decimal("1"), "亿元": Decimal("10000")}.get(fact.unit, Decimal("0"))):
                source_errors.append({"fact_uid": fact.fact_uid, "reference": ref})
    tables = []
    names = {code: name for name, code in TABLE_CODES.items() if not name.startswith("BUD_")}
    for code, name in names.items():
        instances = [t for t in artifact.table_fragments
                     if t.table_code == code and t.extraction_method != "page_text_fallback"]
        record = {"table_code": code, "instances": len(instances),
                  "tables": [{"pages": sorted({c.page_number for c in t.cells}),
                              "rows": len(t.rows), "widths": sorted({len(r) for r in t.rows}),
                              "headers": t.header_row_count, "members": t.source_fragment_uids}
                             for t in instances]}
        try:
            rows = fact_bound_table_rows(SimpleNamespace(parse_artifact=artifact, fact_context=context), name)
            record.update(projection="ready" if rows else "empty", projected_rows=len(rows or []))
        except ValueError as error:
            record.update(projection="blocked", reason=str(error))
        tables.append(record)
    output.mkdir(parents=True, exist_ok=True)
    (output / "artifact.json").write_text(json.dumps(artifact.to_dict(), ensure_ascii=False), encoding="utf-8")
    (output / "fact_context.json").write_text(json.dumps(context.to_dict(), ensure_ascii=False), encoding="utf-8")
    (output / "coverage.json").write_text(json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "check_receipts.json").write_text(json.dumps(receipts, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "issues.json").write_text(json.dumps([vars(i) for i in issues], ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    result = {"filename": path.name, "source_pdf": str(path), "sha256": before,
              "source_unchanged": hashlib.sha256(path.read_bytes()).hexdigest() == before,
              "pages": artifact.page_count, "text_pages": sum(s.has_text_layer for s in artifact.page_states),
              "organization": profile.organization_name.value, "year": profile.year,
              "kind": profile.kind, "facts": len(context.facts),
              "fact_statuses": dict(Counter(f.recognition_status for f in context.facts)),
              "source_errors": source_errors, "tables": tables, "rule_execution": summary,
              "native_blank_observations": len(observations), "native_blank_source_errors": blank_source_errors,
              "disclosure_sources": len(disclosure_sources), "disclosure_source_errors": disclosure_source_errors,
              "native_dimension_cells_recovered": len(dimension_cells),
              "native_dimension_source_errors": dimension_source_errors,
              "completed": coverage["completed_total"], "applicable": coverage["applicable_total"],
              "unresolved": coverage["by_reason"], "elapsed_seconds": round(time.monotonic()-started, 3)}
    (output / "audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main():
    arg = argparse.ArgumentParser(description=__doc__)
    arg.add_argument("--input", type=Path, required=True)
    arg.add_argument("--output", type=Path, required=True)
    options = arg.parse_args()
    paths = sorted(options.input.glob("*.pdf")) if options.input.is_dir() else [options.input]
    results = []
    for index, path in enumerate(paths, 1):
        result = audit(path, options.output / f"document_{index:02}")
        results.append(result)
        print(json.dumps({"file": path.name, "pages": result["pages"],
                          "completed": result["completed"], "applicable": result["applicable"],
                          "blocked_tables": sum(t["projection"] == "blocked" for t in result["tables"]),
                          "source_errors": len(result["source_errors"]),
                          "blank_source_errors": len(result["native_blank_source_errors"])}, ensure_ascii=False), flush=True)
    options.output.mkdir(parents=True, exist_ok=True)
    (options.output / "summary.json").write_text(json.dumps({
        "scope": "native_pdf_development_diagnostic_no_human_truth", "documents": results,
        "completed": sum(r["completed"] for r in results),
        "applicable": sum(r["applicable"] for r in results),
    }, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
