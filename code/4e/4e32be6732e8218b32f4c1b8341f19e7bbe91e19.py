import copy
import csv
import gzip
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker')
BASE=ROOT/'outputs/fp_batch1_20261103'
OUT=BASE/'reviewer_round6_codex_20261103'
BRANCH=Path('/Volumes/workSSD/software development/gbc-worktrees/fp-batch1-20261103')
FILE=BRANCH/'scripts/fp_batch1_reference_exceptions.json'
payload=json.loads(FILE.read_text())
MISSING=object()


def norm(s):
    return re.sub(r'^[一二三四五六七八九十百]+、','',re.sub(r'\s+','',s or ''))


def tokens(path):
    return [k if k else int(i) for k,i in re.findall(r'/([^/\[]+)|\[(\d+)\]',path)]


def get(value,path):
    try:
        for k in tokens(path):value=value[k]
        return value
    except (KeyError,IndexError,TypeError):return MISSING


def put(value,path,new):
    seq=tokens(path)
    for k in seq[:-1]:value=value[k]
    value[seq[-1]]=new


def load(run):
    rows={};entities={};status={}
    for p in (run/'status').glob('*.status.json.gz'):
        doc=p.name.split('.')[0]
        with gzip.open(p,'rt') as h:s=json.load(h)
        status[doc]=s
        report=s['review_report']
        for bucket in ['issues','tips']:
            for x in report.get(bucket,[]):rows.setdefault((doc,x['id']),[]).append(x)
        for x in report.get('manual_review',[]):
            if x.get('finding') and x['finding'].get('id'):
                y=x['finding'];rows.setdefault((doc,y['id']),[]).append(y)
        for bucket in s['result'].get('issues',{}).values():
            if isinstance(bucket,list):
                for x in bucket:
                    if x.get('id'):entities.setdefault((doc,x['id']),[]).append(x)
    return rows,entities,status


def canonical(x):
    x=copy.deepcopy(x)
    for k in ['id','analysis_revision','created_at']:x.pop(k,None)
    if 'message' in x:x['message']=norm(x['message'])
    return x


old,old_entities,old_status=load(BASE/'step0/run1')
new,new_entities,new_status=load(BASE/'round2/gold/run1')
targets=list(csv.DictReader((ROOT/'outputs/gold_baseline_20261102/rev2/fp_batch1_targets.csv').open(encoding='utf-8-sig')))
removed=Counter(x['sample_id'] for x in targets if x['expect_after']=='移除')
parents=defaultdict(list);reviews=[]
for i,e in enumerate(payload['entries'],1):
    doc=e['doc'];oi=e['old_id'].split('|')[-1];ni=e['new_id'].split('|')[-1]
    a=old.get((doc,oi),[]);b=new.get((doc,ni),[])
    checks={'unique_old_entity':len(a)==1,'unique_new_entity':len(b)==1}
    if len(a)==len(b)==1:
        a,b=a[0],b[0]
        checks.update(
            actual_old_value_matches=get(a,e['path'])==e['old_value'],
            actual_new_value_matches=get(b,e['path'])==e['new_value'],
            stable_rule=(a.get('rule_id') or a.get('rule'))==(b.get('rule_id') or b.get('rule'))==e['rule'],
            stable_full_message=norm(a.get('message'))==norm(b.get('message')),
            stable_bbox=a.get('bbox')==b.get('bbox'),
            context_old_message=norm(e['context']['old_message'])==norm(a.get('message')),
            context_new_message=norm(e['context']['new_message'])==norm(b.get('message')),
            context_old_bbox=e['context']['old_bbox']==a.get('bbox'),
            context_new_bbox=e['context']['new_bbox']==b.get('bbox'),
            old_cross_representation=bool(old_entities.get((doc,oi))) and all(get(x,e['path'])==e['old_value'] for x in old_entities[(doc,oi)]),
            new_cross_representation=bool(new_entities.get((doc,ni))) and all(get(x,e['path'])==e['new_value'] for x in new_entities[(doc,ni)]))
        m=re.fullmatch(r'(.+)-(\d+)',e['old_value']);n=re.fullmatch(r'(.+)-(\d+)',e['new_value'])
        checks['same_reference_rule_prefix']=bool(m and n and m[1]==n[1])
        checks['renumber_bounded_by_actual_target_removals']=bool(m and n and 0<int(m[2])-int(n[2])<=removed[doc])
        parents[(doc,oi,ni)].append(e)
    reviews.append({'index':i,'doc':doc,'old_id':e['old_id'],'new_id':e['new_id'],'path':e['path'],
                    'old_value':e['old_value'],'new_value':e['new_value'],'checks':checks,
                    'verdict':'APPROVE' if all(checks.values()) else 'FAIL'})

parent_checks=[]
for (doc,oi,ni),entries in parents.items():
    a,b=canonical(old[(doc,oi)][0]),canonical(new[(doc,ni)][0])
    for e in entries:
        put(a,e['path'],'<EXACT_APPROVED_REFERENCE>');put(b,e['path'],'<EXACT_APPROVED_REFERENCE>')
    parent_checks.append({'doc':doc,'old_id':oi,'new_id':ni,'references':len(entries),
                          'full_remaining_payload_equal':a==b})

new_to_old=defaultdict(set);old_to_new=defaultdict(set)
for e in payload['entries']:
    new_to_old[(e['doc'],e['new_value'])].add(e['old_value'])
    old_to_new[(e['doc'],e['old_value'])].add(e['new_value'])
conflicts=[{'key':k,'values':sorted(v)} for d in [new_to_old,old_to_new] for k,v in d.items() if len(v)>1]
counts=[]
for e in payload['count_exceptions']:
    doc=e['doc'];a=next(x for x in old_status[doc]['review_report']['manual_review'] if x.get('code')=='rules_insufficient_data')
    b=next(x for x in new_status[doc]['review_report']['manual_review'] if x.get('code')=='rules_insufficient_data')
    old_value=get(a,e['path']);new_value=get(b,e['path'])
    actual_removed=sum(x['sample_id']==doc and x['rule']==e['rule_id'] and x['expect_after']=='移除' for x in targets)
    ra,rb=copy.deepcopy(a),copy.deepcopy(b)
    idx=int(re.search(r'\[(\d+)\]',e['path'])[1])
    ra['unresolved_rules'][idx].pop('partial_findings_count',None)
    rb['unresolved_rules'][idx].pop('partial_findings_count',None)
    checks={'old_count_is_one':old_value==1,'new_count_absent_or_zero':new_value is MISSING or new_value==0,
            'one_actual_target_removed':actual_removed==e['removed_count']==1,
            'matching_rule':a['unresolved_rules'][idx]['rule_id']==b['unresolved_rules'][idx]['rule_id']==e['rule_id'],
            'other_notice_fields_identical':ra==rb}
    counts.append({'entry':e,'checks':checks,'verdict':'APPROVE' if all(checks.values()) else 'FAIL'})

result={'exception_file':str(FILE),'sha256':hashlib.sha256(FILE.read_bytes()).hexdigest(),
        'reference_count':len(reviews),'count_exception_count':len(counts),
        'references':reviews,'parent_full_payload_checks':parent_checks,
        'bidirectional_conflicts':conflicts,'counts':counts,
        'ok':all(x['verdict']=='APPROVE' for x in reviews+counts)
             and all(x['full_remaining_payload_equal'] for x in parent_checks) and not conflicts}
(OUT/'exceptions_independent_review.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print({'ok':result['ok'],'reference_count':len(reviews),'parent_count':len(parent_checks),
       'reference_failures':[x for x in reviews if x['verdict']!='APPROVE'],
       'parent_failures':[x for x in parent_checks if not x['full_remaining_payload_equal']],
       'counts':counts,'conflicts':conflicts})
