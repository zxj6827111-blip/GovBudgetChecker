"""重新核对三格缺标签原件与两批核心范围外候选，保持宽口径分母。"""
import hashlib
import json
from pathlib import Path
import re
import sys

import pymupdf

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
sys.path.insert(0, str(ROOT))
from src.services.pdf_parser import PDFParser
from src.services.document_profile_resolver import resolve_document_profile
from src.engine.fact_context import build_fact_context_from_artifact
from src.services.table_import_receipt import build_table_import_receipt


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + '\n')


def compact(text):
    return re.sub(r'\s+', '', text)


records = []
legacy = json.loads((ROOT / 'outputs/native_structure_repair_20261015/source_label_missing.json').read_text())
for entry in legacy:
    source = Path(entry['source_pdf'])
    assert hashlib.sha256(source.read_bytes()).hexdigest() == entry['sha256']
    artifact = PDFParser(None).build_parse_artifact(str(source))
    texts = ['\n'.join(block.text for block in artifact.text_blocks if block.page_number == page)
             for page in range(1, artifact.page_count + 1)]
    profile = resolve_document_profile(filename=source.name, page_texts=texts)
    context = build_fact_context_from_artifact(artifact, org_name=profile.organization_name.value,
                                              fiscal_year=profile.year, report_kind=profile.kind)
    receipt = build_table_import_receipt(artifact, context.facts, org_name=profile.organization_name.value)
    for expected in entry['unbound_money_cells']:
        actual = [cell for cell in receipt['reading_quality']['unbound_money_cells']
                  if cell['source_cell_id'] == expected['source_cell_id'] and cell['page'] == expected['page']]
        assert len(actual) == 1
        actual = actual[0]
        assert actual['raw_text'] == expected['raw_text']
        assert actual['reason'] == 'source_label_missing' and actual['fact_count'] == 0
        assert not [fact for fact in context.facts for ref in fact.extra_dims.get('evidence_refs', [])
                    if ref.get('source_cell_id') == expected['source_cell_id']]
        with pymupdf.open(source) as pdf:
            page = pdf[expected['page'] - 1]
            box = pymupdf.Rect(expected['bbox'])
            # 原始glyph框跨行时get_textbox会带入下一行，按来源格内词中心选唯一词，不按金额相等搜索。
            words = [word for word in page.get_text('words')
                     if box.x0 <= (word[0] + word[2]) / 2 <= box.x1
                     and box.y0 <= (word[1] + word[3]) / 2 <= box.y1]
            assert len(words) == 1
            native = words[0][4]
        assert compact(native) == compact(expected['raw_text'])
        records.append(dict(filename=source.name, source_pdf=str(source), sha256=entry['sha256'],
                            page=expected['page'], raw_text=expected['raw_text'], bbox=expected['bbox'],
                            independent_source_text=native, retained=actual, fabricated_subject=False))
    out = BASE / 'unlabelled' / entry['sha256']
    out.mkdir(parents=True, exist_ok=True)
    write(out / 'receipt.json', receipt)
assert len(records) == 3
write(BASE / 'unlabelled_reverification.json', dict(status='passed', retained_unlabelled_cells=3, results=records))

retained = []
totals = {}
fields = ['amount_cell_count', 'identified_fiscal_amount_cell_count', 'unclassified_numeric_projection_count',
          'unresolved_money_cell_count', 'fact_missing_count', 'rule_blocked_count', 'rule_admitted_count']
for phase in ['new10', 'old15']:
    counts = dict.fromkeys(fields, 0)
    for folder in sorted((BASE / phase / 'documents').iterdir()):
        receipt = json.loads((folder / 'receipt.json').read_text())
        for field in fields:
            counts[field] += receipt[field]
        for item in receipt['reading_quality'].get('unbound_money_cells', []):
            row = dict(batch=phase, doc_id=folder.name, **item)
            with pymupdf.open(folder / 'source.pdf') as pdf:
                box = pymupdf.Rect(item['bbox']) + (-80, -15, 160, 15)
                row['independent_source_context'] = pdf[item['page'] - 1].get_textbox(box)
            row['review'] = '核心范围外数字候选；保留待复核，不计入核心格已通过数'
            if folder.name == 'SH2024NEW_07' and item['page'] == 8 and item['raw_text'] == '3':
                row['review'] = '原第8页为所属单位名单，3是序号；原回执候选继续保留，不静默改分母'
                row['visual_evidence'] = str(BASE / 'images/SH2024NEW_07_p8.png')
            retained.append(row)
    totals[phase] = counts
assert totals['new10']['amount_cell_count'] == 8902
assert totals['old15']['amount_cell_count'] == 11611
assert len(retained) == 22
write(BASE / 'retained_unresolved_candidates.json', retained)
write(BASE / 'receipt_totals.json', totals)
print(json.dumps(dict(unlabelled_retained=3, outside_core_candidates_retained=22, receipt_totals=totals), ensure_ascii=False))
