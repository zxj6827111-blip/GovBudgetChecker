import gzip
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker')
BASE=ROOT/'outputs/fp_batch1_20261103'
OUT=BASE/'reviewer_round2_codex_20261103'


def norm(s):
    return re.sub(r'^[一二三四五六七八九十百]+、','',re.sub(r'\s+','',s or ''))


def clean(value):
    if isinstance(value,dict):
        return {k:clean(v) for k,v in value.items() if k not in {'pre_group_issue_id'}}
    if isinstance(value,list):
        return [clean(v) for v in value]
    return value


def load(run):
    groups=defaultdict(list)
    for p in (run/'status').glob('*.status.json.gz'):
        sid=p.name.split('.')[0]
        with gzip.open(p,'rt') as h:s=json.load(h)
        report=s['review_report']
        items=[]
        for bucket in ['issues','tips']:
            for x in report.get(bucket) or []:items.append((bucket,x))
        for rec in report.get('manual_review') or []:
            if rec.get('record_type') in {'finding_review_candidate','ai_candidate'}:
                items.append(('manual_review',rec.get('finding') or {}))
        for bucket,x in items:
            key=(sid,bucket,x.get('rule_id') or x.get('rule'),norm(x.get('message')))
            fields=['severity','title','location','bbox','page_number','evidence','evidence_status',
                    'evidence_ids','evidence_verification','evidence_explanation','metrics',
                    'obligation_ids','check_relation_id','source_binding_status','source_binding_method']
            groups[key].append({f:clean(x.get(f)) for f in fields})
    return groups


results={}
for label,oldrun,newrun in [
    ('gold',BASE/'step0/run1',BASE/'round2/gold/run1'),
    ('nongold',BASE/'round2/nongold/old',BASE/'round2/nongold/new')]:
    before,after=load(oldrun),load(newrun)
    changed=[];matched=0
    for k in sorted(set(before)&set(after)):
        a=Counter(json.dumps(v,ensure_ascii=False,sort_keys=True) for v in before[k])
        b=Counter(json.dumps(v,ensure_ascii=False,sort_keys=True) for v in after[k])
        matched+=sum((a&b).values())
        if a!=b:
            changed.append({'key':k,'before':before[k],'after':after[k]})
    results[label]={'matched_full_evidence_fingerprints':matched,'changed':changed}
(OUT/'raw_evidence_fingerprints.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
print({k:{'matched':v['matched_full_evidence_fingerprints'],'changed':len(v['changed'])} for k,v in results.items()})
