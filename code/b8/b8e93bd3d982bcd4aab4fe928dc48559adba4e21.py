"""复核79格裁定的原生字符证明，并汇总扩大回执中的未裁定项。"""
import hashlib
import json
from pathlib import Path
import re
import unicodedata

import pdfplumber

BASE = Path(__file__).resolve().parent
FROZEN = BASE.parent / 'shanghai_2024_v23_acceptance_20261015'


def compact(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value))


def write(name, value):
    (BASE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2))


def main():
    rows = json.loads((BASE / 'identity_adjudication/source_identity_adjudications.json').read_text())
    results = []
    for doc_id in sorted({row['doc_id'] for row in rows}):
        path = FROZEN / 'documents' / doc_id / 'source.pdf'
        with pdfplumber.open(path) as pdf:
            for row in (row for row in rows if row['doc_id'] == doc_id):
                assert hashlib.sha256(path.read_bytes()).hexdigest() == row['source_pdf_sha256']
                pieces = []
                for proof in row['source_proofs']:
                    chars = pdf.pages[row['page'] - 1].chars
                    segment = chars[proof['start_char_index']:proof['end_char_index']]
                    assert ''.join(char['text'] for char in segment) == proof['text']
                    for source, actual in zip(proof['characters'], segment, strict=True):
                        assert source['text'] == actual['text']
                        box = [actual['x0'], actual['top'], actual['x1'], actual['bottom']]
                        assert all(abs(a - b) < .0001 for a, b in zip(source['bbox'], box, strict=True))
                    assert proof['source_cell_bbox'][1] - .25 <= segment[0]['top'] < proof['source_cell_bbox'][3] - .25
                    pieces.append((proof['source_column'], compact(proof['text'])))
                names = [value for _, value in sorted(pieces) if not value.isdigit()]
                codes = [value for _, value in sorted(pieces) if value.isdigit()]
                name = ''.join(names) or None
                code = ''.join(codes) or ('total' if name and '合计' in name else None)
                assert name == row['subject_name'] and code == row['subject_code'], row['truth_id']
                results.append(dict(truth_id=row['truth_id'], subject_code=code, subject_name=name,
                                    source_proofs_reverified=True))
    assert len(results) == 79
    write('source_identity_proof_recheck.json', dict(verified=len(results), reads_tested_facts=False,
          human_independent_signoff=False, normalization='NFKC and whitespace only', results=results))
    projections = []
    counts = {}
    for doc in json.loads((FROZEN / 'inventory.json').read_text()):
        receipt = json.loads((BASE / 'final_retest/documents' / doc['doc_id'] / 'receipt.json').read_text())
        fields = ['amount_cell_count', 'identified_fiscal_amount_cell_count', 'fact_present_count',
                  'fact_bound_count', 'rule_admitted_count', 'pending_review_count',
                  'unclassified_numeric_projection_count']
        counts[doc['doc_id']] = {key: receipt.get(key) for key in fields}
        for table in receipt['tables']:
            for item in table.get('unresolved_money_cells', []):
                projections.append(dict(doc_id=doc['doc_id'], filename=doc['filename'],
                                        table_code=table['table_code'], **item))
    write('receipts_fresh_summary.json', counts)
    write('pending_numeric_projections.json', projections)
    print('SOURCE_IDENTITY_PROOFS_VERIFIED', len(results))
    print('PENDING_NUMERIC_PROJECTIONS', len(projections))
    for item in projections:
        print(item['doc_id'], item['page'], item['raw_text'], item.get('observation_kind'), item.get('reason'))


if __name__ == '__main__':
    main()
