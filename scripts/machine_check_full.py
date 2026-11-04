# -*- coding: utf-8 -*-
"""B1 完整机器核验：24项/四态/证据/自由发现/公式Decimal/单位/重复计数/源SHA。"""
import json, glob, os, re, sys, hashlib
from decimal import Decimal, InvalidOperation
import fitz

OUT=sys.argv[1]; PDFS=sys.argv[2]
pdfs=json.load(open(PDFS,encoding='utf-8'))
CHECKS=["GS-S01","GS-S02","GS-S03","GS-S04","GS-T01","GS-T02","GS-T03","GS-T04","GS-T05","GS-T06","GS-T07","GS-T08","GS-X01","GS-X02","GS-N01","GS-N02","GS-N03","GS-N04","GS-N05","GS-N06","GS-N07","GS-N08","GS-N09","GS-W01"]

def dec(s):
    s=s.replace(',','').replace('，','').strip()
    return Decimal(s)

def parse_sum(formula):
    """解析 'a + b + c = d' 或 '... ≠ e'；返回 (lhs, rhs) 或 None。"""
    if not formula: return None
    f=formula.replace('，',',').replace('−','-')
    m=re.search(r'([0-9][0-9,\.]*(?:\s*\+\s*[0-9][0-9,\.]*)+)\s*(?:=|≠)\s*([0-9][0-9,\.]*)', f)
    if not m: return None
    try:
        lhs=sum((dec(x) for x in m.group(1).split('+')), Decimal('0'))
        return lhs, dec(m.group(2))
    except InvalidOperation:
        return None

res={"ok":True,"samples":{},"duplicates":[]}
for p in sorted(glob.glob(os.path.join(OUT,'annotations','*','annotation.json'))):
    a=json.load(open(p,encoding='utf-8')); sid=a['sample_id']
    doc=fitz.open(pdfs[sid]); texts=[pg.get_text() for pg in doc]
    actual=hashlib.sha256(open(pdfs[sid],'rb').read()).hexdigest()
    problems=[]; warnings=[]; formula_checks=[]
    if sorted(c['check_id'] for c in a['checks'])!=sorted(CHECKS): problems.append('24 check ids')
    if a.get('sha256')!=actual: problems.append('source sha mismatch')
    if a.get('review_status')!='PENDING_REVIEW': warnings.append('review_status not PENDING_REVIEW')
    if 'truth_frozen_at' in a or 'reviewed_by' in a: problems.append('premature freeze fields')
    seen_quotes={}
    for c in a['checks']:
        v=c.get('verdict')
        if v not in ('pass','defect','not_applicable','cannot_determine'): problems.append(f"{c['check_id']} bad verdict {v}")
        if v in ('pass','defect') and not (c.get('items') or []): problems.append(f"{c['check_id']} {v} no items")
        if v=='defect' and not c.get('reason'): problems.append(f"{c['check_id']} defect no reason")
        for it in c.get('items') or []:
            iv=it.get('verdict')
            if iv in ('pass','defect') and not (it.get('values') or []): problems.append(f"{c['check_id']} item {iv} no values")
            if iv=='defect':
                if not it.get('formula') and c['check_id'] not in ('GS-S03','GS-S04'): problems.append(f"{c['check_id']} defect no formula")
                parsed=parse_sum(it.get('formula'))
                if parsed:
                    lhs,rhs=parsed
                    formula_checks.append({"check":c['check_id'],"label":it.get('label'),"lhs":str(lhs),"rhs":str(rhs),"equal":lhs==rhs})
                else:
                    warnings.append(f"{c['check_id']} formula unparsed")
            for val in it.get('values') or []:
                pg=val.get('page'); q=val.get('quote') or ''
                if not isinstance(pg,int) or pg<1 or pg>len(texts):
                    problems.append(f"{c['check_id']} bad page {pg}"); continue
                if q and q not in texts[pg-1]:
                    problems.append(f"{c['check_id']} quote not found p{pg}: {q[:30]}")
                seen_quotes.setdefault((pg,q),[]).append(c['check_id'])
                unit=val.get('unit')
                if unit and unit not in ('万元','元','个','人','人次','辆','批次','台','套','%',''):
                    warnings.append(f"{c['check_id']} unit {unit}")
    for f in a.get('free_findings') or []:
        pg=f.get('page'); q=f.get('quote') or ''
        if isinstance(pg,int) and 1<=pg<=len(texts) and q and q not in texts[pg-1]:
            problems.append(f"FF {f.get('id')} quote not found p{pg}: {q[:30]}")
        for occ in f.get('occurrences') or []:
            op=occ.get('page'); oq=occ.get('quote') or ''
            if isinstance(op,int) and 1<=op<=len(texts) and oq and oq not in texts[op-1]:
                problems.append(f"FF {f.get('id')} occurrence not found p{op}: {oq[:30]}")
    # duplicate quote across checks (same page+quote) -> warning, not necessarily error
    for (pg,q),cs in seen_quotes.items():
        if len(set(cs))>1:
            res['duplicates'].append({"sample_id":sid,"page":pg,"quote":q[:40],"checks":sorted(set(cs))})
    res['samples'][sid]={"problems":problems,"warnings":warnings,"formula_checks":formula_checks,
                          "checks":len(a['checks']),"defects":[c['check_id'] for c in a['checks'] if c['verdict']=='defect'],
                          "free_findings":len(a.get('free_findings') or [])}
    if problems: res['ok']=False
json.dump(res,open(os.path.join(OUT,'machine_checks_full.json'),'w',encoding='utf-8'),ensure_ascii=False,indent=2)
print(json.dumps({"ok":res['ok'],"samples":{k:{"problems":v['problems'],"warnings":v['warnings'][:5],"defects":v['defects']} for k,v in res['samples'].items()}},ensure_ascii=False,indent=1))
