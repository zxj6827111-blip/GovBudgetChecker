"""独立于表格解析器，以原始字符顺序核查所有冻结金额是否被网格截断。"""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

import pdfplumber

BASE = Path(__file__).resolve().parent
NUMBER = re.compile(r'-?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{1,4}')


def runs(chars, band):
    selected = [(i, char) for i, char in enumerate(chars)
                if str(char.get('text') or '').strip()
                and band[0] - .2 <= (char['x0'] + char['x1']) / 2 < band[2] + .2]
    groups = []
    for entry in selected:
        if not groups or entry[0] != groups[-1][-1][0] + 1:
            groups.append([])
        groups[-1].append(entry)
    return groups


def main():
    records = []
    for doc in json.loads((BASE / 'inventory.json').read_text()):
        folder = BASE / 'documents' / doc['doc_id']
        truth = json.loads((folder / 'preparse_source_truth.json').read_text())
        with pdfplumber.open(folder / 'source.pdf') as pdf:
            cache = {}
            for index, item in enumerate(truth):
                box = item.get('source_cell_bbox') or item['bbox']
                key = (item['page'], round(box[0], 3), round(box[2], 3))
                if key not in cache:
                    cache[key] = runs(pdf.pages[item['page'] - 1].chars, box)
                margin = .5 if item.get('source_cell_bbox') else 4
                owned = [group for group in cache[key]
                         if box[1] - margin <= group[0][1]['top'] < box[3] + (margin if not item.get('source_cell_bbox') else -.5)]
                numeric = [group for group in owned if NUMBER.fullmatch(''.join(char['text'] for _, char in group))]
                status = 'native_confirmed' if len(numeric) == 1 else 'native_object_unresolved'
                record = dict(truth_id=f"{doc['doc_id']}:{index:05}", doc_id=doc['doc_id'], page=item['page'],
                              source_code=item['expected'].get('subject_code'), source_name=item['expected'].get('subject_name'),
                              frozen_grid_amount=item['raw_text'], bbox=item['bbox'], source_cell_bbox=item.get('source_cell_bbox'),
                              status=status)
                if len(numeric) == 1:
                    group = numeric[0]
                    literal = ''.join(char['text'] for _, char in group)
                    record.update(native_amount=literal, grid_truncated=literal != item['raw_text'],
                                  source_proof=dict(start_char_index=group[0][0], end_char_index=group[-1][0] + 1,
                                      text=literal, bbox=[min(c['x0'] for _, c in group), min(c['top'] for _, c in group),
                                                         max(c['x1'] for _, c in group), max(c['bottom'] for _, c in group)],
                                      characters=[dict(index=i,text=c['text'],bbox=[c['x0'],c['top'],c['x1'],c['bottom']]) for i,c in group]))
                else:
                    record['owned_object_texts'] = [''.join(c['text'] for _, c in group) for group in owned]
                records.append(record)
    summary = dict(recorded_at=datetime.now(timezone.utc).isoformat(), checks=len(records),
                   statuses=dict(Counter(r['status'] for r in records)),
                   truncated_grid_amounts=sum(r.get('grid_truncated', False) for r in records),
                   reads_tested_artifact_or_facts=False)
    (BASE / 'native_amount_source_audit.json').write_text(json.dumps(dict(summary=summary,records=records),ensure_ascii=False,indent=2))
    print(json.dumps(summary,ensure_ascii=False))
    for record in records:
        if record.get('grid_truncated') or record['status'] != 'native_confirmed':
            print(json.dumps({k:v for k,v in record.items() if k!='source_proof'},ensure_ascii=False))


if __name__ == '__main__':
    main()
