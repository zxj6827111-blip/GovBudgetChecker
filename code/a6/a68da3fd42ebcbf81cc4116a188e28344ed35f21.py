"""冻结原件参照 vs 真实服务数据库回读；差异原样保留，不修改产品。"""
from collections import Counter,defaultdict
from decimal import Decimal,InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import unicodedata

BASE=Path(__file__).resolve().parent


def norm(text):
    return re.sub(r'\s+','',unicodedata.normalize('NFKC',str(text or '')))


def num(text):
    text=norm(text).replace(',','').replace('−','-')
    if not re.fullmatch(r'-?\d+(?:\.\d+)?',text):return None
    try:return Decimal(text)
    except InvalidOperation:return None


def decoded(value):
    return json.loads(value) if isinstance(value,str) else value


def refs(fact):
    return decoded(fact.get('extra_dims') or {}).get('evidence_refs',[])


def distance(a,b):
    return max(abs(x-y) for x,y in zip(a,b))


def indexed_cells(artifact,key):
    result=defaultdict(list)
    for table in artifact[key]:
        if table.get('extraction_method')=='page_text_fallback':continue
        for cell in table['cells']:
            if cell.get('bbox'):
                result[cell['page_number']].append((table,cell))
    return result


def matches(index,page,box,tolerance=1.3):
    choices=[(distance(box,c['bbox']),t,c) for t,c in index.get(page,[]) if distance(box,c['bbox'])<=tolerance]
    return sorted(choices,key=lambda x:x[0])


def decimal_tokens(page):
    # 不依赖闭合网格，额外查续页顶部开口行和单元格外溢文字里的金额。
    lines=[]
    for c in sorted(page['chars'],key=lambda c:(c['origin'][1],c['origin'][0])):
        if lines and abs(c['origin'][1]-lines[-1][0]['origin'][1]) < 1.6:lines[-1].append(c)
        else:lines.append([c])
    result=[]
    for line in lines:
        runs=[]
        for c in sorted(line,key=lambda c:c['bbox'][0]):
            if runs and c['bbox'][0]-runs[-1][-1]['bbox'][2] <= 4:runs[-1].append(c)
            else:runs.append([c])
        for run in runs:
            text=''.join(c['text'] for c in run)
            for m in re.finditer(r'(?<![\d.])[-−]?\d[\d,]*\.\d+(?![\d.])',text):
                cs=run[m.start():m.end()]
                box=[min(c['bbox'][0] for c in cs),min(c['bbox'][1] for c in cs),max(c['bbox'][2] for c in cs),max(c['bbox'][3] for c in cs)]
                result.append(dict(text=m.group(),bbox=box))
    return result


def facts_at(facts,page,box):
    return [f for f in facts if any(r.get('physical_page')==page and r.get('bbox') and distance(r['bbox'],box)<=1.3 for r in refs(f))]


def execute(id,inventory):
    source=json.loads((BASE/'source'/f'{id}.json').read_text())
    artifact=json.loads((BASE/'system'/id/'artifact.json').read_text())
    facts=json.loads((BASE/'system'/id/'facts.json').read_text())
    raw=indexed_cells(artifact,'source_grids');physical=indexed_cells(artifact,'physical_fragments')
    stats=Counter();differences=[];cell_details=[];tokens=[];semantic=[];tables=[];anchors=[]
    core_pages={p:table for table in inventory['tables'] for p in table['pages']}
    for table in inventory['tables']:
        pages=table['pages']
        rows=[]
        for p in pages:
            found=sorted({t.get('table_code') for t,c in physical[p] if t.get('table_code') and t.get('table_code').startswith(f'FIN_{table["number"]:02d}_')})
            rows.append(dict(page=p,matched_codes=found))
            if not found:differences.append(dict(type='table_page_missing_or_misclassified',table=table['title'],page=p))
        tables.append(dict(**table,comparison=rows))
    for page in source['pages']:
        p=page['page']
        for i,cell in enumerate(page['cells']):
            orig=norm(cell['text']);value=num(cell['text'])
            marker='blank' if not orig else ('number' if value is not None else 'text')
            found=matches(raw,p,cell['bbox'])
            stats['cells']+=1;stats[marker+'_cells']+=1
            record=dict(page=p,source_index=i,source=cell,marker=marker)
            if not found:
                record['status']='source_grid_missing';differences.append(dict(type=record['status'],**record))
            else:
                _,t,c=found[0]
                observed=c.get('grid_raw_text') if c.get('grid_raw_text') is not None else c['raw_text']
                record.update(system_raw=observed,system_bbox=c['bbox'],system_cell=f'{t["fragment_uid"]}:r{c["row_idx"]}c{c["col_idx"]}')
                if orig==norm(observed):
                    stats['literal_match']+=1;record['status']='literal_match'
                elif value is not None and value==num(observed):
                    stats['numeric_format_only']+=1;record['status']='numeric_format_only'
                else:
                    record['status']='text_or_value_difference';differences.append(dict(type=record['status'],**record))
            cell_details.append(record)
        if p not in core_pages:continue
        for token in decimal_tokens(page):
            x0,y0,x1,y1=token['bbox'];x=(x0+x1)/2;y=(y0+y1)/2
            nearby=[(t,c) for t,c in physical[p] if c['bbox'][0]-1<=x<=c['bbox'][2]+1 and c['bbox'][1]-1<=y<=c['bbox'][3]+1]
            agreeing=[(t,c) for t,c in nearby if num(c['raw_text'])==num(token['text'])]
            check=dict(page=p,source=token,matched=bool(agreeing))
            if not agreeing:
                check['observed']=[dict(text=c['raw_text'],bbox=c['bbox']) for t,c in nearby]
                differences.append(dict(type='independent_decimal_not_in_selected_cells',**check))
            else:
                t,c=agreeing[0];fs=facts_at(facts,p,c['bbox'])
                check.update(system_raw=c['raw_text'],fact_count=len(fs),fact_uids=[f['fact_uid'] for f in fs])
            tokens.append(check)
        table=core_pages[p];n=table['number']
        if n not in [2,3,5]:continue
        grouped=defaultdict(list)
        for c in page['cells']:grouped[c['bbox'][1]].append(c)
        for cells in grouped.values():
            cells=sorted(cells,key=lambda c:c['bbox'][0])
            if len(cells)<4:continue
            code=norm(cells[0]['text'])
            if code=='合计':
                label='合计';code='total';amount_cells=cells[1:]
            elif re.fullmatch(r'\d{3,7}',code):
                label=norm(cells[1]['text']);amount_cells=cells[2:]
            else:continue
            for col,c in enumerate(amount_cells):
                value=num(c['text']);fs=facts_at(facts,p,c['bbox'])
                check=dict(page=p,table=n,subject_code=code,subject_name=label,source_col_offset=col,raw=c['text'],bbox=c['bbox'])
                if not norm(c['text']):
                    check.update(status='blank_preserved' if not fs else 'blank_became_fact',observed=[f['fact_uid'] for f in fs])
                elif value is not None:
                    agreeing=[f for f in fs if f.get('subject_code')==code and norm(f.get('subject_name'))==label and num(f.get('normalized_value'))==value and f.get('unit')=='万元']
                    # 明确核验支出三列的业务指标，防止引用同格但指标语义错配。
                    if n in [3,5] and col<3:
                        expected=['total_actual','basic_actual','project_actual'][col]
                        check['indicator']=expected
                        agreeing=[f for f in agreeing if f.get('indicator')==expected]
                    check.update(status='bound_match' if agreeing else 'binding_difference',observed=[dict(fact_uid=f['fact_uid'],subject_code=f['subject_code'],subject_name=f['subject_name'],indicator=f['indicator'],normalized_value=f['normalized_value']) for f in fs])
                else:continue
                semantic.append(check)
    # 目视标注的完整三公数据行：空白保留，数值、预算/决算和分项列都按原列位置比较。
    travel_page=inventory['tables'][6]['pages'][0]
    candidates=[t for t in artifact['physical_fragments'] if t['page_number']==travel_page and str(t.get('table_code') or '').startswith('FIN_07_') and t.get('extraction_method')!='page_text_fallback']
    for t in candidates:
        rows=defaultdict(list)
        for c in t['cells']:rows[c['row_idx']].append(c)
        data=[sorted(cs,key=lambda c:c['col_idx']) for cs in rows.values() if len(cs)==12 and any(num(c['raw_text']) is not None for c in cs)]
        if not data:continue
        cells=data[-1]
        for k,(value,c) in enumerate(zip(inventory['fin07_source_order_12_values'],cells)):
            observed=num(c['raw_text']);fs=facts_at(facts,travel_page,c['bbox'])
            status=('blank_preserved' if not norm(c['raw_text']) and not fs else 'mismatch') if value is None else ('value_match' if Decimal(value)==observed else 'mismatch')
            anchors.append(dict(page=travel_page,table=7,column=k,expected=value,system_raw=c['raw_text'],status=status,facts=[dict(indicator=f['indicator'],budget_stage=f.get('budget_stage'),period=f.get('period'),extra_dims=decoded(f.get('extra_dims') or {})) for f in fs]))
        break
    if len(anchors)!=12:differences.append(dict(type='manual_travel_row_missing',page=travel_page))
    total=inventory.get('fin05_total_anchor')
    if total:
        p,*values=total
        total_rows=[s for s in semantic if s['table']==5 and s['page']==p and s['subject_code']=='total']
        for col,value in enumerate(values):
            found=[s for s in total_rows if s['source_col_offset']==col]
            anchors.append(dict(page=p,table=5,column=col,expected=value,status='manual_total_match' if found and ((value is None and found[0]['status']=='blank_preserved') or (value is not None and num(found[0]['raw'])==Decimal(value) and found[0]['status']=='bound_match')) else 'mismatch',comparison=found))
    result=dict(id=id,filename=source['filename'],pages=len(source['pages']),core_tables=len(tables),core_table_pages=len(core_pages),source_cells=dict(stats),decimal_tokens=len(tokens),decimal_matched=sum(t['matched'] for t in tokens),semantic_checks=dict(Counter(s['status'] for s in semantic)),manual_anchor_checks=dict(Counter(a['status'] for a in anchors)),differences=differences,source_unchanged=hashlib.sha256(Path(source['source_pdf']).read_bytes()).hexdigest()==source['sha256'],parser=artifact['parser_fingerprint'])
    (BASE/f'{id}_comparison.json').write_text(json.dumps(dict(summary=result,tables=tables,cells=cell_details,decimal_tokens=tokens,semantic=semantic,anchors=anchors),ensure_ascii=False,indent=2))
    return result


if __name__=='__main__':
    frozen=json.loads((BASE/'source_frozen_manifest.json').read_text())
    for item in frozen['documents']:
        assert hashlib.sha256((BASE/'source'/f'{item["id"]}.json').read_bytes()).hexdigest()==item['source_truth_sha256']
    assert hashlib.sha256((BASE/'visual_source_truth.json').read_bytes()).hexdigest()==(BASE/'visual_source_truth.sha256').read_text().strip()
    visual=json.loads((BASE/'visual_source_truth.json').read_text())
    results=[]
    for inventory in visual['documents']:
        result=execute(inventory['id'],inventory);results.append(result)
        print(json.dumps(result,ensure_ascii=False)[:5000],flush=True)
    (BASE/'comparison_initial_summary.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
