"""只读原PDF：候选定位仅作导航，重新提取原页/格；不导入业务解析器。"""
import collections
import gzip
import hashlib
import json
import pathlib
import subprocess
import pymupdf as fitz

ROOT=pathlib.Path(__file__).resolve().parents[1]
CAND=pathlib.Path('/Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106')
EV=CAND/'outputs/unified_remediation_20261106'
hist=json.loads((EV/'historical24_dispositions.json').read_text())
m12=json.loads((EV/'m12_lineage.json').read_text())
targets=collections.defaultdict(set)
for row in hist:
    targets[row['sample_id']].add(1)
    def visit(v):
        if isinstance(v,dict):
            page=v.get('physical_page',v.get('page'))
            if isinstance(page,int):targets[row['sample_id']].add(page)
            for val in v.values():visit(val)
        elif isinstance(v,list):
            for val in v:visit(val)
    visit(row.get('source_positions',[]));visit(row.get('source_evidence',{}))
for row in m12:
    for ref in row.get('original_evidence',[]):
        if ref.get('physical_page'):targets[row['sample_id']].add(ref['physical_page'])
for sid,pages in {'RB-2023-U04':[13,19,20],'RB-2025-U02':[12,18,19],
    'RB-2025-U01':[1,12,18],'RB-2025-U17':[1,16,23],
    'RA-036':[19],'RB-2022-U11':[19],'RB-2025-D05':[20],'RB-2025-U03':[18],
    'SP-ORIG-01':[15,16],'SP-ORIG-04':[14,15], 'SP-ORIG-07':[12,18], 'SP-ORIG-08':[20,21,22,23,24,25]}.items():
    targets[sid].update(pages)
ref14=json.loads((EV/'ledger.json').read_text())['historical_items']['reference_debt_14']
for row in ref14:targets[row['sample_id']].add(row['key'][-1])
index=[];blank_results=[]
for sid,pageset in sorted(targets.items()):
    pdfs=list((EV/'kit_cover_bound_r2/pdfs'/sid).glob('*.pdf')) or list((EV/'kit/pdfs'/sid).glob('*.pdf'))
    assert len(pdfs)==1,sid
    pdf=pdfs[0];sha=hashlib.sha256(pdf.read_bytes()).hexdigest()
    out=ROOT/'originals'/sid;out.mkdir(exist_ok=True)
    with fitz.open(pdf) as doc:
        # 全文是独立原件语境来源，不把候选回执当作事实池。
        (out/'full_text.txt').write_text('\n'.join(f'\n=== PHYSICAL PAGE {i+1} ===\n'+p.get_text(sort=True) for i,p in enumerate(doc)))
        for page in sorted(pageset):
            if not 1<=page<=len(doc):continue
            text=doc[page-1].get_text(sort=True)
            (out/f'p{page}.txt').write_text(text)
            # Poppler原页图，不使用执行者提供的截图。
            subprocess.run(['pdftoppm','-f',str(page),'-l',str(page),'-scale-to','1500','-png','-singlefile',str(pdf),str(out/f'p{page}')],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            index.append({'sample_id':sid,'pdf_path':str(pdf),'pdf_sha256':sha,'physical_page':page,
                'page_text_sha256':hashlib.sha256(text.encode()).hexdigest(),'image':str(out/f'p{page}.png')})
        def check(v,item):
            if isinstance(v,dict):
                box=v.get('bbox');page=v.get('physical_page',v.get('page'))
                if box and isinstance(page,int) and 1<=page<=len(doc):
                    got=doc[page-1].get_textbox(box)
                    blank_results.append({'item':item,'sample_id':sid,'pdf_sha256':sha,'physical_page':page,
                        'bbox':box,'actual_original_text':got,'blank':not got.strip(),
                        'literal_row':v.get('literal_row'),'literal_column_header':v.get('literal_column_header'),
                        'source_cell_id_locator_only':v.get('source_cell_id'),
                        'review_limit':'位置来自候选导航；原页格与行列须另行视觉核验，不以提取空字符串独立签收'})
                for val in v.values():check(val,item)
            elif isinstance(v,list):
                for val in v:check(val,item)
        for i,row in enumerate(hist,1):
            if row['sample_id']==sid:
                assert row['pdf_sha256']==sha
                check(row.get('source_positions'),i)
(ROOT/'receipts/original_page_index.json').write_text(json.dumps(index,ensure_ascii=False,indent=2))
(ROOT/'receipts/original_cell_reextraction.json').write_text(json.dumps(blank_results,ensure_ascii=False,indent=2))
print(json.dumps({'pages':len(index),'position_boxes':len(blank_results),'nonblank_boxes':sum(not r['blank'] for r in blank_results)}))
