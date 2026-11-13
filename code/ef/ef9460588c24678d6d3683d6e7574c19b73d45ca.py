"""只读取原件、独立网格及待复核坐标，绝不读取被测facts或用其补真值。"""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import unicodedata

import pdfplumber

BASE=Path(__file__).resolve().parent
FROZEN=BASE.parent/'shanghai_2024_v23_acceptance_20261015'
OUT=BASE/'identity_adjudication'


def compact(value):return re.sub(r'\s+','',unicodedata.normalize('NFKC',str(value or '')))


def runs(chars,band):
    selected=[(i,c) for i,c in enumerate(chars) if str(c.get('text') or '').strip()
              and band[0]-.2 <= (c['x0']+c['x1'])/2 < band[2]+.2]
    groups=[]
    for entry in selected:
        if not groups or entry[0]!=groups[-1][-1][0]+1:groups.append([])
        groups[-1].append(entry)
    return groups


def main():
    requests=OUT/'source_only_requests.json'
    if not requests.exists():
        # 仅取修改前记录的原页坐标和表种；不传递任何actual_facts值到裁定步骤。
        rows=[]
        for folder in sorted((FROZEN/'documents').iterdir()):
            for r in json.loads((folder/'comparison.json').read_text()):
                if r['fact_status']=='manual_review':
                    rows.append({k:r[k] for k in ('truth_id','doc_id','page','oracle_table','source_row','source_col','table_code')})
        requests.write_text(json.dumps(rows,ensure_ascii=False,indent=2))
    entries=json.loads(requests.read_text());results=[]
    for doc in sorted({r['doc_id'] for r in entries}):
        source=FROZEN/'documents'/doc/'source.pdf'
        oracle=json.loads((source.parent/'source_oracle.json').read_text())
        with pdfplumber.open(source) as pdf:
            for request in [r for r in entries if r['doc_id']==doc]:
                page=next(p for p in oracle['pages'] if p['page']==request['page'])
                table=next(t for t in page['tables'] if t['index']==request['oracle_table'])
                row=request['source_row']; col=request['source_col']
                prefix = {'FIN_03_expenditure': 6, 'FIN_05_general_public_expenditure': 3}.get(request['table_code'])
                label_cols = (list(range(table['col_count'] - prefix)) if prefix
                              else list(range(0 if col < table['col_count']//2 else table['col_count']//2, col)))
                # 每个文字对象保持原生连续字符顺序，以对象首字落格决定归属；
                # 跨横线的末字不会按中心坐标被错塞进下一行。组内不补字、不查科目字典。
                proofs=[];pieces=[];uncertain=[]
                for label_col in label_cols:
                    box=table['cell_boxes'][row][label_col]
                    if not box:continue
                    groups=runs(pdf.pages[request['page']-1].chars,box)
                    owned=[g for g in groups if box[1]-.25<=g[0][1]['top']<box[3]-.25]
                    if len(owned)>1:
                        uncertain.append(f'multiple_native_objects_in_label_column_{label_col}')
                    for group in owned:
                        literal=''.join(c['text'] for _,c in group)
                        # 原件对象位置异常时不硬拼到跨两行之外。
                        if max(c['bottom'] for _,c in group)>box[3]+2*(box[3]-box[1]):
                            uncertain.append('native_object_spans_multiple_rows')
                        pieces.append((label_col,compact(literal)))
                        proofs.append({'source_column':label_col,'source_cell_bbox':box,
                            'start_char_index':group[0][0],'end_char_index':group[-1][0]+1,'text':literal,
                            'first_glyph_top':group[0][1]['top'],
                            'characters':[{'index':i,'text':c['text'],'bbox':[c['x0'],c['top'],c['x1'],c['bottom']]} for i,c in group]})
                codes=[s for _,s in sorted(pieces) if s.isdigit()]
                names=[s for _,s in sorted(pieces) if not s.isdigit()]
                name=''.join(names) or None
                code=''.join(codes) or ('total' if name and '合计' in name else None)
                if not name:uncertain.append('native_subject_name_missing')
                results.append({**request,'status':'manual_review' if uncertain else 'source_confirmed',
                    'subject_code':code,'subject_name':name,'reasons':uncertain,
                    'method':'independent_source_only_native_object_order_and_original_row_start',
                    'source_pdf_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                    'source_proofs':proofs,'rendering':f'{doc}_p{request["page"]}.png',
                    'reviewer':'本轮代理原页图像及原生字符核对；非独立人工签署'})
    (OUT/'source_identity_adjudications.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
    summary=dict(total=len(results),status=dict(Counter(r['status'] for r in results)),
                 source_only_inputs=True,tested_facts_used_as_truth=False,human_independent_signoff=False)
    (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    print(summary)
    for r in results:
        if r['status']!='source_confirmed':print(r['truth_id'],r['reasons'])


if __name__=='__main__':main()
