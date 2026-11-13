"""在运行系统前核对原件标签的连续原生字符；不以系统事实裁定科目。"""
from collections import Counter
import importlib.util
import json
from pathlib import Path
import re

import pdfplumber

BASE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('source_reader', BASE / 'compare_source.py')
source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(source)


def groups(chars, box):
    chosen = [(index, char) for index,char in enumerate(chars) if str(char['text']).strip()
              and box[0]-.2 <= (char['x0']+char['x1'])/2 < box[2]+.2]
    runs = []
    for item in chosen:
        if not runs or item[0] != runs[-1][-1][0]+1:
            runs.append([])
        runs[-1].append(item)
    return [run for run in runs if box[1]-.25 <= run[0][1]['top'] < box[3]-.25]


overlays = {}
native = json.loads((BASE / 'native_amount_source_audit.json').read_text())
for row in native['records']:
    if row.get('grid_truncated'):
        overlays[row['truth_id']] = dict(truth_id=row['truth_id'], raw_text=row['native_amount'],
            source_grid_amount=row['frozen_grid_amount'], amount_proof=row['source_proof'], status='source_confirmed',
            reason='独立原格金额与连续原字符不一致；保留原格并单列字符裁定')
records = []
for doc in json.loads((BASE / 'inventory.json').read_text()):
    folder = BASE / 'documents' / doc['doc_id']
    oracle = json.loads((folder / 'source_oracle.json').read_text())
    truth = json.loads((folder / 'preparse_source_truth.json').read_text())
    with pdfplumber.open(folder / 'source.pdf') as pdf:
        for index,item in enumerate(truth):
            truth_id = f"{doc['doc_id']}:{index:05}"
            identity = item['expected']
            record = dict(truth_id=truth_id, page=item['page'], expected=identity)
            code = item['table_code'].split('_')[1]
            if code == '07':
                record['status'] = 'source_header_confirmed'
                records.append(record)
                continue
            page = oracle['pages'][item['page']-1]
            table = next((t for t in page['tables'] if t['index'] == item['oracle_table']), None)
            if table is None or item['source_row'] is None:
                record['status'] = 'source_grid_identity_unresolved'
                records.append(record)
                continue
            ri,ci,ncols = item['source_row'],item['source_col'],table['col_count']
            if code in ['02','03','05','08','09']:
                schema = source.source_columns(code,table)
                counts = {'02':7,'03':6,'05':3,'08':6,'09':6}
                start = min(schema) if schema else ncols-counts[code]
                columns = range(start)
            elif code == '06':
                columns = range(0 if ci<ncols//2 else ncols//2,ci)
            else:
                columns = [0 if ci==1 else 2]
            pieces = []
            proofs = []
            safe = True
            for column in columns:
                box = table['cell_boxes'][ri][column]
                if box is None:
                    continue
                runs = groups(pdf.pages[item['page']-1].chars,box)
                usable = len(runs)==1 and max(c['bottom'] for _,c in runs[0])-min(c['top'] for _,c in runs[0]) <= (box[3]-box[1])+18
                if not usable:
                    words = [word for word in page['words'] if box[0] <= (word[0]+word[2])/2 < box[2]
                             and box[1] <= (word[1]+word[3])/2 < box[3]]
                    text = source.compact(''.join(word[4] for word in sorted(words,key=lambda w:(round(w[1],1),w[0]))))
                    if text:
                        if code in ['02','03','05','08','09'] and column < start-1 and text in ['类','款','项']:
                            proofs.append(dict(column=column,excluded_header_marker=text,source_words=words))
                        else:
                            pieces.append((column,text))
                            proofs.append(dict(column=column,text=text,method='independent_native_word_center',source_words=words))
                    continue
                for run in runs:
                    if max(c['bottom'] for _,c in run)-min(c['top'] for _,c in run) > (box[3]-box[1])+18:
                        safe = False
                    text = ''.join(char['text'] for _,char in run)
                    marker = code in ['02','03','05','08','09'] and column < start-1 and source.compact(text) in ['类','款','项']
                    if not marker:
                        pieces.append((column,source.compact(text)))
                    proofs.append(dict(column=column,start_char_index=run[0][0],end_char_index=run[-1][0]+1,text=text,
                        excluded_header_marker=marker,
                        characters=[dict(index=i,text=c['text'],bbox=[c['x0'],c['top'],c['x1'],c['bottom']]) for i,c in run]))
            subject_code = ''.join(text for _,text in sorted(pieces) if text.isdigit()) or None
            subject_name = ''.join(text for _,text in sorted(pieces) if not text.isdigit()) or None
            if subject_code is None and subject_name and '合计' in subject_name and code not in ['01','04']:
                subject_code = 'total'
            record.update(native_subject_code=subject_code,native_subject_name=subject_name,source_identity_proofs=proofs)
            differs = (subject_code,subject_name) != (identity['subject_code'],identity['subject_name'])
            if safe and subject_name and (subject_code or code in ['01','04']):
                record['status'] = 'native_identity_confirmed'
                if differs:
                    overlay = overlays.setdefault(truth_id,dict(truth_id=truth_id,status='source_confirmed'))
                    overlay.update(expected=dict(subject_code=subject_code,subject_name=subject_name),
                        source_identity_proofs=proofs,reason='原格标签的连续原字符及首字行归属；独立于被测系统')
                    record['status'] = 'native_identity_overlay'
            else:
                record['status'] = 'native_identity_unresolved'
            records.append(record)
report = dict(source_only_before_tested_parse=True, statuses=dict(Counter(r['status'] for r in records)),records=records)
(BASE / 'source_identity_audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
(BASE / 'source_truth_overlay.json').write_text(json.dumps(dict(reads_tested_facts=False,overlays=list(overlays.values())),ensure_ascii=False,indent=2))
print(json.dumps(dict(statuses=report['statuses'],overlays=len(overlays)),ensure_ascii=False))
