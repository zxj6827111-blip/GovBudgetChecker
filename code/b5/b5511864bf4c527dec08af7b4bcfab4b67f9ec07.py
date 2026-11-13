"""原页 lines 网格向前核对；保存漏格，不以解析事实数量替代原件覆盖。

网格抽取本身仍使用 pdfplumber，因此此结果不是人工标注的独立识别准确率。
"""
from collections import defaultdict
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
import time

import pdfplumber

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.engine.fact_context import build_fact_context_from_artifact
from src.schemas.parse_artifact import ParseArtifact
from src.services.document_profile_resolver import resolve_document_profile
from src.services.pdf_parser import PDFParser, DEFAULT_TABLE_SETTINGS
from src.services.table_import_receipt import build_table_import_receipt, source_cell_id
from src.engine.amount_math import normalize_amount_to_wan

BASE = Path(__file__).resolve().parent
inventory = json.loads((ROOT / 'outputs/putuo_fresh_batch_20261014/inventory.json').read_text())
lock = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (ROOT / 'src').rglob('*.py')}
(BASE / 'corpus_source_fingerprints.json').write_text(json.dumps(lock, indent=2))
results = []
for index, item in enumerate(inventory, 1):
    started = time.monotonic()
    result = dict(item)
    try:
        path = Path(item['source_pdf'])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item['sha256']
        originals = []
        with pdfplumber.open(path) as pdf:
            texts = [p.extract_text() or '' for p in pdf.pages]
            # 原网格在运行解析器前冻结；包含非核心组织名录表和真正的空表。
            for page_no, page in enumerate(pdf.pages, 1):
                for grid in page.find_tables(table_settings=DEFAULT_TABLE_SETTINGS[0][1]):
                    rows = grid.extract() or []
                    if max((len(row) for row in rows), default=0) < 2:
                        continue
                    originals.append((page_no, rows, grid))
            artifact = PDFParser(None).build_parse_artifact_from_handle(pdf, file_hash=item['sha256'])
            cells = defaultdict(list)
            for table in artifact.source_grids:
                if table.extraction_method == 'page_text_fallback':
                    continue
                for cell in table.cells:
                    if cell.bbox:
                        cells[cell.page_number].append((table, cell))
            source_cells, source_numeric, matched, mismatches = 0, 0, 0, []
            numeric = PDFParser(None)._parse_numeric
            for page_no, rows, grid in originals:
                for r, row in enumerate(rows):
                    for c, raw in enumerate(row):
                        box = grid.rows[r].cells[c]
                        if box is None:
                            continue  # 合并占位没有独立原格，不能计作漏格或零。
                        text = str(raw or '').strip()
                        source_cells += 1
                        source_numeric += numeric(text) is not None
                        candidates = [(table, cell) for table, cell in cells[page_no]
                                      if all(abs(a - b) < .51 for a, b in zip(box, cell.bbox))]
                        exact = [(table, cell) for table, cell in candidates
                                 if str(cell.grid_raw_text if cell.grid_raw_text is not None else cell.raw_text).strip() == text]
                        if exact:
                            matched += 1
                        else:
                            mismatches.append({'page': page_no, 'row': r, 'col': c, 'raw_text': text,
                                               'bbox': list(box), 'candidates': [cell.raw_text for _, cell in candidates]})
        profile = resolve_document_profile(filename=path.name, page_texts=texts)
        context = build_fact_context_from_artifact(artifact, org_name=profile.organization_name.value,
                    fiscal_year=profile.year, report_kind=profile.kind, doc_type='dept_final')
        facts = context.facts if context else []
        receipt = build_table_import_receipt(artifact, facts, org_name=profile.organization_name.value)
        payload = artifact.to_dict()
        assert ParseArtifact.from_dict(json.loads(json.dumps(payload))).to_dict() == payload
        lookup = {source_cell_id(table, cell): cell for table, cell in artifact.cells()}
        fact_source_errors = []
        for fact in facts:
            refs = [ref for ref in fact.extra_dims.get('evidence_refs', []) if ref.get('source_cell_id')]
            if len(refs) != 1 or refs[0]['source_cell_id'] not in lookup:
                fact_source_errors.append(fact.fact_uid)
                continue
            cell = lookup[refs[0]['source_cell_id']]
            if (cell.raw_text.strip() != fact.raw_value or cell.page_number != refs[0]['physical_page']
                    or normalize_amount_to_wan(cell.raw_text.replace(" ", "").replace("\u3000", ""), fact.unit) != fact.normalized_value):
                fact_source_errors.append(fact.fact_uid)
        result.update(pages=artifact.page_count, scan_pages=sum(not p.has_text_layer for p in artifact.page_states),
            original_grid_count=len(originals), original_cell_count=source_cells, original_numeric_cell_count=source_numeric,
            original_matched_count=matched, original_mismatches=mismatches,
            organization=profile.organization_name.value, organization_evidence=profile.organization_name.evidence,
            fact_count=len(facts), fact_source_errors=fact_source_errors,
            serialized_roundtrip_equal=True, reading_import={key: value for key, value in receipt.items() if key != 'tables'},
            reading_gaps=[t for t in receipt['tables'] if t['unresolved_money_cells'] or not t['matrix_complete']],
            source_unchanged=hashlib.sha256(path.read_bytes()).hexdigest() == item['sha256'],
            elapsed_seconds=round(time.monotonic() - started, 2))
        # 只对存在缺口的原件保留完整解析对象以便逐格追查。
        if mismatches or fact_source_errors or result['reading_gaps'] or not result['organization']:
            directory = BASE / 'corpus_gaps' / item['sha256'][:16]
            directory.mkdir(parents=True, exist_ok=True)
            (directory / 'artifact.json').write_text(json.dumps(payload, ensure_ascii=False))
            (directory / 'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    except Exception as exc:
        result['error'] = type(exc).__name__ + ': ' + str(exc)
    results.append(result)
    (BASE / 'corpus_results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(json.dumps({'index': index, 'total': len(inventory), 'file': item['filename'],
                      'pages': result.get('pages'), 'mismatches': len(result.get('original_mismatches', [])),
                      'unbound': result.get('reading_import', {}).get('unresolved_money_cell_count'),
                      'error': result.get('error')}, ensure_ascii=False), flush=True)
drift = [name for name, digest in lock.items() if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest]
(BASE / 'corpus_source_drift.json').write_text(json.dumps(drift))
assert not drift, 'Source changed during validation'
