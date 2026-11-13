"""复核初轮差异：逻辑续表、可视裁切与原始内容流、全表金额行身份。"""
from collections import Counter,defaultdict
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re

from pypdf import PdfReader

from compare import BASE,norm,num,refs,decoded,distance,matches,indexed_cells,facts_at,decimal_tokens


def extract_original_labels(text):
    # 只由原件内容流提取名称，不能使用系统给出的名称来拼参照。
    labels=defaultdict(set)
    pattern=r'(?m)^\s*(\d{3,7}(?:[ \t]+\d{2})?)[ \t]+([^\d]+?)[ \t\r\n]+[-−]?\d[\d,]*\.\d+'
    for match in re.finditer(pattern,text):
        labels[norm(match[1])].add(norm(match[2]))
    return {code:next(iter(values)) for code,values in labels.items() if len(values)==1}


def check_identity(facts,p,cell,code,label,indicator=None):
    fs=facts_at(facts,p,cell['bbox'])
    good=[f for f in fs if f.get('subject_code')==code and norm(f.get('subject_name'))==label
          and num(f.get('normalized_value'))==num(cell['text']) and f.get('unit')=='万元'
          and (indicator is None or f.get('indicator')==indicator)]
    return dict(page=p,bbox=cell['bbox'],raw=cell['text'],subject_code=code,subject_name=label,indicator=indicator,
                status='bound_match' if good else 'binding_difference',observed=[{k:f.get(k) for k in ['fact_uid','subject_code','subject_name','indicator','normalized_value']} for f in fs])


def run(id,inventory):
    source=json.loads((BASE/'source'/f'{id}.json').read_text())
    artifact=json.loads((BASE/'system'/id/'artifact.json').read_text())
    facts=json.loads((BASE/'system'/id/'facts.json').read_text())
    initial=json.loads((BASE/f'{id}_comparison.json').read_text())
    reader=PdfReader(source['source_pdf'])
    texts=[p.extract_text() or '' for p in reader.pages]
    supplemental=BASE/'source'/f'{id}_pypdf_content_stream.json'
    supplemental.write_text(json.dumps(dict(reason='Original-source adjudication after cross-engine differences; includes content clipped by rendering',pages=texts),ensure_ascii=False))
    bypage={n:extract_original_labels(text) for n,text in enumerate(texts,1)}
    logical=indexed_cells(artifact,'table_fragments')
    physical=indexed_cells(artifact,'physical_fragments')
    table_checks=[];row_checks=[];semantic=[];adjudications=[];new_differences=[];blank_checks=[]
    for table in inventory['tables']:
        n=table['number']
        for p in table['pages']:
            actual=sorted({t.get('table_code') for t,c in logical[p] if str(t.get('table_code') or '').startswith(f'FIN_{n:02}_')})
            table_checks.append(dict(table=n,page=p,logical_codes=actual,status='match' if actual else 'missing'))
            page=source['pages'][p-1]
            groups=defaultdict(list)
            for c in page['cells']:groups[c['bbox'][1]].append(c)
            for cells in groups.values():
                cells=sorted(cells,key=lambda c:c['bbox'][0])
                if n in [2,3,5] and len(cells)>=4:
                    code=norm(cells[0]['text'])
                    first_money=next((k for k,c in enumerate(cells) if num(c['text']) is not None and ('.' in norm(c['text']) or norm(c['text'])=='0')),None)
                    total_label=''.join(norm(c['text']) for c in cells[:first_money]) if first_money is not None else None
                    if code=='合计' or total_label in ['合计','类款项合计']:
                        code='total';label='合计';amounts=cells[1:] if norm(cells[0]['text'])=='合计' else cells[first_money:]
                    elif re.fullmatch(r'\d{3,7}',code):
                        label=bypage[p].get(code,norm(cells[1]['text']));amounts=cells[2:]
                    else:continue
                    row_checks.append(dict(table=n,page=p,subject_code=code,label=label,source_cells=len(cells)))
                    for col,cell in enumerate(amounts):
                        if num(cell['text']) is None:
                            if not norm(cell['text']):blank_checks.append(dict(page=p,bbox=cell['bbox'],status='blank_preserved' if not facts_at(facts,p,cell['bbox']) else 'blank_became_fact'))
                            continue
                        indicator=({2:['total_actual','fiscal_allocation','superior_subsidy_income','business_income','operational_income','subsidiary_income','other_income'],3:['total_actual','basic_actual','project_actual','to_superior_actual','operational_actual','subsidiary_actual'],5:['total_actual','basic_actual','project_actual']}[n][col])
                        check=check_identity(facts,p,cell,code,label,indicator)
                        check['table']=n;check['column_offset']=col;semantic.append(check)
                elif n==6:
                    mid=(min(c['bbox'][0] for c in page['cells'])+max(c['bbox'][2] for c in page['cells']))/2
                    for k,cell in enumerate(cells):
                        if num(cell['text']) is None or ('.' not in norm(cell['text']) and norm(cell['text'])!='0'):continue
                        half=(cell['bbox'][0]+cell['bbox'][2])/2 < mid
                        # 双栏各自有编码/名称/金额，左金额为空也不能把左名称带入右栏。
                        left=[c for c in cells[:k] if ((c['bbox'][0]+c['bbox'][2])/2 < mid)==half]
                        codes=[norm(c['text']) for c in left if re.fullmatch(r'\d{2,5}',norm(c['text']))]
                        names=[norm(c['text']) for c in left if norm(c['text']) and num(c['text']) is None]
                        if not names:
                            semantic.append(dict(page=p,table=6,bbox=cell['bbox'],raw=cell['text'],status='source_missing_label',observed=[f['fact_uid'] for f in facts_at(facts,p,cell['bbox'])]))
                            continue
                        label=''.join(names);code=''.join(codes) or ('total' if '合计' in label else None)
                        label=bypage[p].get(code,label)
                        check=check_identity(facts,p,cell,code,label,'total_actual');check['table']=6;semantic.append(check)
                elif n in [1,4]:
                    label=None
                    for cell in cells:
                        if num(cell['text']) is not None:
                            fs=facts_at(facts,p,cell['bbox'])
                            if label is None:
                                semantic.append(dict(page=p,table=n,bbox=cell['bbox'],raw=cell['text'],status='source_missing_label',observed=[f['fact_uid'] for f in fs]))
                            else:
                                good=[f for f in fs if norm(f.get('subject_name'))==label and num(f.get('normalized_value'))==num(cell['text']) and f.get('unit')=='万元']
                                semantic.append(dict(page=p,table=n,bbox=cell['bbox'],raw=cell['text'],subject_name=label,status='bound_match' if good else 'binding_difference',observed=[{k:f.get(k) for k in ['fact_uid','subject_code','subject_name','indicator','normalized_value']} for f in fs]))
                        elif norm(cell['text']):label=norm(cell['text'])
    # 真如城运两张续页首行没有顶边：原件文本与图像可读，不能被闭合网格参照漏掉。
    if id=='doc10':
        for p,n in [(7,2),(9,3)]:
            tokens=[t for t in decimal_tokens(source['pages'][p-1]) if t['bbox'][1]<130 and t['bbox'][0]>260]
            top=min(t['bbox'][1] for t in tokens)
            tokens=[t for t in tokens if t['bbox'][1]<=top+3]
            tokens=sorted(tokens,key=lambda t:t['bbox'][0])
            assert len(tokens)==(2 if n==2 else 3),tokens
            expected_code='2120199';label=bypage[p][expected_code]
            row_checks.append(dict(table=n,page=p,subject_code=expected_code,label=label,source='original_open_top_row'))
            for col,token in enumerate(tokens):
                box=token['bbox'];x=(box[0]+box[2])/2;y=(box[1]+box[3])/2
                choices=[c for t,c in physical[p] if c['bbox'][0]<=x<=c['bbox'][2] and c['bbox'][1]<=y<=c['bbox'][3] and num(c['raw_text'])==num(token['text'])]
                assert len(choices)==1
                cell=dict(text=token['text'],bbox=choices[0]['bbox'])
                indicator=({2:['total_actual','fiscal_allocation'],3:['total_actual','basic_actual','project_actual']}[n][col])
                check=check_identity(facts,p,cell,expected_code,label,indicator)
                check.update(table=n,column_offset=col,original_token_bbox=box,source='original_open_top_row')
                semantic.append(check)
    # 初轮依赖物理片段的续页误报，必须通过逻辑表和逐格来源明确消解。
    for diff in initial['summary']['differences']:
        if diff['type']=='table_page_missing_or_misclassified':
            found=[t for t in table_checks if t['page']==diff['page'] and t['status']=='match']
            adjudications.append(dict(initial=diff,resolution='continuation_is_present_in_stitched_logical_table' if found else 'unresolved',evidence=found))
        elif diff['type']=='text_or_value_difference':
            p=diff['page'];found=matches(physical,p,diff['source']['bbox'])
            t,c=found[0][1:] if found else (None,None)
            final_text=norm(c['raw_text']) if c else None
            # 连续原件内容流支持最终名称；单元格画面裁切另保留截图证据。
            supported=bool(final_text and final_text in norm(texts[p-1]))
            adjudications.append(dict(initial=diff,resolution='render_clipping_or_cross_row_text_final_label_supported_by_original_content_stream' if supported else 'unresolved',final_system_text=final_text,original_pypdf_support=supported))
        else:new_differences.append(diff)
    # 手工标注的三公完整数据行同时核验业务列及预算/决算身份。
    travel=[]
    for anchor in initial['anchors']:
        if anchor['table']!=7:continue
        col=anchor['column'];expected=['total_actual','overseas','vehicle_purchase_operation','vehicle_purchase','vehicle_operation','reception'][col//2]
        fs=anchor['facts']
        good=(not fs) if anchor['expected'] is None else bool(len(fs)==1 and fs[0]['indicator']==expected and norm(fs[0]['extra_dims'].get('source_header','')).endswith('预算数' if col%2==0 else '决算数'))
        travel.append(dict(page=anchor['page'],column=col,expected=anchor['expected'],expected_indicator=expected,status='match' if good and anchor['status']!='mismatch' else 'mismatch'))
    result=dict(id=id,filename=source['filename'],pages=len(source['pages']),table_checks=dict(Counter(t['status'] for t in table_checks)),semantic_checks=dict(Counter(c['status'] for c in semantic)),blank_checks=dict(Counter(c['status'] for c in blank_checks)),travel_checks=dict(Counter(c['status'] for c in travel)),adjudications=dict(Counter(a['resolution'] for a in adjudications)),remaining_initial_differences=new_differences,supplemental_sha256=hashlib.sha256(supplemental.read_bytes()).hexdigest())
    (BASE/f'{id}_adjudicated.json').write_text(json.dumps(dict(summary=result,tables=table_checks,semantic=semantic,rows=row_checks,blanks=blank_checks,travel=travel,adjudications=adjudications),ensure_ascii=False,indent=2))
    return result


if __name__=='__main__':
    visual=json.loads((BASE/'visual_source_truth.json').read_text())
    results=[]
    for inventory in visual['documents']:
        result=run(inventory['id'],inventory);results.append(result);print(json.dumps(result,ensure_ascii=False),flush=True)
    (BASE/'adjudicated_summary.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
