"""仅使用冻结原件、原页词和原始字符裁定；不读取被测facts/artifact。"""
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import unicodedata

import pdfplumber

BASE = Path(__file__).resolve().parent


def compact(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(text or '')))


def runs(chars, box):
    selected = [(i,c) for i,c in enumerate(chars) if str(c['text']).strip()
                and box[0]-.2 <= (c['x0']+c['x1'])/2 < box[2]+.2]
    groups = []
    for entry in selected:
        if not groups or entry[0] != groups[-1][-1][0]+1:
            groups.append([])
        groups[-1].append(entry)
    return groups


def main():
    overlays = {}
    native = json.loads((BASE/'native_amount_source_audit.json').read_text())
    for entry in native['records']:
        if entry.get('grid_truncated'):
            overlays[entry['truth_id']] = dict(truth_id=entry['truth_id'], raw_text=entry['native_amount'],
                source_grid_amount=entry['frozen_grid_amount'], reason='原网格截断跨横线的金额尾字；以原生连续字符为准',
                amount_proof=entry['source_proof'], status='source_confirmed')
    notes = []
    for doc in json.loads((BASE/'inventory.json').read_text()):
        folder = BASE/'documents'/doc['doc_id']
        oracle = json.loads((folder/'source_oracle.json').read_text())
        truth = json.loads((folder/'preparse_source_truth.json').read_text())
        with pdfplumber.open(folder/'source.pdf') as pdf:
            for index, item in enumerate(truth):
                identity = item['expected']
                truth_id = f"{doc['doc_id']}:{index:05}"
                if identity['subject_name'] is None:
                    # 原件缺上边框，独立网格没有纳入第一行；按同一原生字基线读完整行。
                    page = oracle['pages'][item['page']-1]
                    center = (item['bbox'][1]+item['bbox'][3])/2
                    line = [w for w in page['words'] if abs((w[1]+w[3])/2-center)<2]
                    amounts = sorted([w for w in line if re.fullmatch(r'-?[\d,]+\.\d+', compact(w[4]))],key=lambda w:w[0])
                    maps = {'FIN_02_income':['total_actual','fiscal_allocation','superior_subsidy_income','business_income','operational_income','subsidiary_income','other_income'],
                            'FIN_03_expenditure':['total_actual','basic_actual','project_actual','to_superior_actual','operational_actual','subsidiary_actual'],
                            'FIN_05_general_public_expenditure':['total_actual','basic_actual','project_actual']}
                    indicators = maps[item['table_code']]
                    labels = [w for w in line if amounts and w[2] < amounts[0][0]]
                    code = ''.join(compact(w[4]) for w in labels if compact(w[4]).isdigit())
                    name = ''.join(compact(w[4]) for w in labels if not compact(w[4]).isdigit())
                    matched = [i for i,w in enumerate(amounts) if abs(w[0]-item['bbox'][0])<.5 and compact(w[4])==item['raw_text']]
                    assert len(amounts)==len(indicators) and len(matched)==1 and code and name,truth_id
                    overlays[truth_id] = dict(truth_id=truth_id, expected=dict(subject_code=code,subject_name=name,indicator=indicators[matched[0]]),
                        reason='原件开放顶行的独立词坐标与同一字基线，金额列顺序由原表固定列声明验证',
                        native_word_proofs=line,status='source_confirmed')
                subjects = {'SH2024NEW_05':{'30108','30109'},'SH2024NEW_06':{'2080506','2080599'}}
                if identity.get('subject_code') not in subjects.get(doc['doc_id'],set()):
                    continue
                if (doc['doc_id'],item['page']) not in {('SH2024NEW_05',15),('SH2024NEW_06',18)}:
                    continue
                page = oracle['pages'][item['page']-1]
                table = next(t for t in page['tables'] if t['index']==item['oracle_table'])
                row,col = item['source_row'],item['source_col']
                if item['table_code']=='FIN_06_basic_expenditure':
                    cols = list(range(0 if col<table['col_count']//2 else table['col_count']//2,col))
                else:
                    cols = list(range(table['col_count']-3))
                pieces=[];proofs=[]
                for label_col in cols:
                    box=table['cell_boxes'][row][label_col]
                    if not box:
                        continue
                    owned=[g for g in runs(pdf.pages[item['page']-1].chars,box)
                           if box[1]-.25<=g[0][1]['top']<box[3]-.25]
                    assert len(owned)<=1,(truth_id,label_col)
                    for group in owned:
                        text=''.join(c['text'] for _,c in group)
                        pieces.append((label_col,compact(text)))
                        proofs.append(dict(source_column=label_col,start_char_index=group[0][0],end_char_index=group[-1][0]+1,
                                           text=text,characters=[dict(index=i,text=c['text'],bbox=[c['x0'],c['top'],c['x1'],c['bottom']]) for i,c in group]))
                code=''.join(s for _,s in sorted(pieces) if s.isdigit())
                name=''.join(s for _,s in sorted(pieces) if not s.isdigit())
                assert code and name,truth_id
                overlays[truth_id]=dict(truth_id=truth_id,expected=dict(subject_code=code,subject_name=name),
                    reason='原生文字对象连续顺序及首字归属纠正独立网格的跨线截字',source_identity_proofs=proofs,status='source_confirmed')
    result=dict(recorded_at=datetime.now(timezone.utc).isoformat(),reads_tested_facts=False,
                original_preparse_truth_untouched=True, human_independent_signoff=False, overlays=list(overlays.values()))
    (BASE/'source_truth_overlay.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print('SOURCE_OVERLAYS',len(overlays),'AMOUNT',sum('raw_text' in r for r in overlays.values()),'IDENTITY',sum('expected' in r for r in overlays.values()))


if __name__=='__main__':
    main()
