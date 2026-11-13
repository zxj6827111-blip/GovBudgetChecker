import csv
import gzip
import hashlib
import json
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path('/Volumes/workSSD/software development/GovBudgetChecker')
BASE = ROOT / 'outputs/fp_batch1_20261103'
OUT = BASE / 'reviewer_round2_codex_20261103'
BRANCH = Path('/Volumes/workSSD/software development/gbc-worktrees/fp-batch1-20261103')


def read(p):
    if str(p).endswith('.gz'):
        with gzip.open(p, 'rt') as h:
            return json.load(h)
    return json.loads(p.read_text())


def norm(v):
    return re.sub(r'^[一二三四五六七八九十百]+、', '', re.sub(r'\s+', '', str(v or '')))


def extract(run, nongold=False):
    records = []
    ledgers = {}
    for path in sorted((run / 'status').glob('*.status.json.gz')):
        sid = path.name.split('.')[0]
        status = read(path)
        todo = read(run / ('records' if nongold else 'todo') / (sid + ('.json.gz' if nongold else '.todo.json.gz')))
        if nongold:
            todo = todo['todo']
        report = status['review_report']
        for bucket in ['issues', 'tips']:
            for x in report.get(bucket) or []:
                severity = str(x.get('severity') or '').lower().strip()
                if x.get('evidence_status') == 'degraded_missing_evidence':
                    tier = 'D'
                elif severity in {'critical','fatal','error','high','warn','warning','medium','low'}:
                    tier = 'F'
                elif x.get('review_kind') == 'review_notice' or x.get('source') == 'structured_ingest':
                    tier = 'N'
                elif x.get('review_kind') != 'manual_review' and severity != 'manual_review':
                    tier = 'R'
                else:
                    tier = 'P'
                records.append((sid, tier, str(x.get('rule_id') or ''), str(x.get('id')), norm(x.get('message')), x))
        for x in report.get('manual_review') or []:
            if x.get('record_type') == 'unfinished_check':
                continue
            if x.get('record_type') in {'finding_review_candidate','ai_candidate'}:
                y = x.get('finding') or {}
                tier = 'D' if y.get('evidence_status') == 'degraded_missing_evidence' else 'P'
                records.append((sid,tier,str(x.get('code') or y.get('rule_id') or ''),str(y.get('id')),norm(y.get('message')),y))
            else:
                records.append((sid,'N',str(x.get('code')),str(x.get('code')),norm(x.get('message')),x))
        instances = {i['obligation_id']: i for i in status['check_coverage']['instances']}
        ledgers[sid] = {'instances':instances,'todo':todo}
        for oid in sorted(set(todo['blocking_ids'])):
            x = instances[oid]
            records.append((sid,'O',oid,oid,norm(x.get('detail')),x))
    return records, ledgers


def compare(old, new):
    # 完整原文，不使用交付比较器的 400/600 字截断或 0.90 相似度。
    before = Counter((sid,tier,rule,msg) for sid,tier,rule,raw,msg,x in old)
    after = Counter((sid,tier,rule,msg) for sid,tier,rule,raw,msg,x in new)
    removed, added = before-after, after-before
    return {'old_count':len(old),'new_count':len(new),
            'old_tiers':dict(Counter(x[1] for x in old)),
            'new_tiers':dict(Counter(x[1] for x in new)),
            'removed':[{'key':list(k),'count':n} for k,n in removed.items()],
            'added':[{'key':list(k),'count':n} for k,n in added.items()]}


snapshot = {}
for folder, commit in [('code','c6cd033d'),('round2/after_code','1b513d84')]:
    entries = subprocess.check_output(['git','ls-tree','-rz',commit],cwd=BRANCH).decode().split('\0')
    mismatches = []
    checked = 0
    for line in entries:
        if not line:
            continue
        meta,name = line.split('\t',1)
        mode,kind,sha = meta.split()
        if kind != 'blob' or mode == '120000':
            continue
        p = BASE/folder/name
        if not p.is_file():
            mismatches.append({'path':name,'reason':'missing'});continue
        data=p.read_bytes()
        actual=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
        checked += 1
        if actual != sha:
            mismatches.append({'path':name,'expected':sha,'actual':actual})
    snapshot[folder]={'commit':commit,'checked_files':checked,'mismatches':mismatches}

frozen=list(csv.DictReader((ROOT/'gold/FROZEN.csv').open(encoding='utf-8-sig')))
gold_hashes={'count':len(frozen),'mismatches':[]}
for row in frozen:
    actual=hashlib.sha256((ROOT/'gold'/row['sample_id']/'annotation.json').read_bytes()).hexdigest()
    if actual != row['annotation_sha256']:
        gold_hashes['mismatches'].append(row['sample_id'])

r2,lr2=extract(ROOT/'outputs/gold_baseline_20261102/run1')
step,ls=extract(BASE/'step0/run1')
after,la=extract(BASE/'round2/gold/run1')
oldng,lon=extract(BASE/'round2/nongold/old',True)
newng,lnn=extract(BASE/'round2/nongold/new',True)
ledger_changes={}
for label,before,current in [('gold',ls,la),('nongold',lon,lnn)]:
    changes=[]
    for sid in before:
        bi,ci=before[sid]['instances'],current[sid]['instances']
        for oid in sorted(set(bi)|set(ci)):
            b,c=bi.get(oid),ci.get(oid)
            if b!=c:
                fields=[k for k in sorted(set(b or {})|set(c or {})) if (b or {}).get(k)!=(c or {}).get(k)]
                changes.append({'sample':sid,'obligation':oid,'fields':fields,'before':b,'after':c})
        b,c=before[sid]['todo']['blocking_ids'],current[sid]['todo']['blocking_ids']
        if sorted(b)!=sorted(c):
            changes.append({'sample':sid,'todo_before':b,'todo_after':c})
    ledger_changes[label]=changes

result={'snapshots':snapshot,'frozen_gold':gold_hashes,
        'step0_full_text':compare(r2,step),'gold_full_text':compare(step,after),
        'nongold_full_text':compare(oldng,newng),
        'ledger_changes':ledger_changes}
(OUT/'independent_artifact_audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
for name in ['step0_full_text','gold_full_text','nongold_full_text']:
    r=result[name]
    print(name,{k:v for k,v in r.items() if k not in {'removed','added'}},
          'removed',sum(x['count'] for x in r['removed']),'added',sum(x['count'] for x in r['added']))
print('snapshots',{k:{'checked_files':v['checked_files'],'mismatch_count':len(v['mismatches'])} for k,v in snapshot.items()})
print('gold_hashes',gold_hashes)
print('ledger_changes',{k:len(v) for k,v in ledger_changes.items()})
