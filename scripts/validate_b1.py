# -*- coding: utf-8 -*-
import csv, glob, hashlib, json, os, sys
OUT=sys.argv[1]; PDFS=sys.argv[2]
pdfs=json.load(open(PDFS,encoding='utf-8'))
ok=[]; bad=[]
def chk(name,cond,detail=''):
    (ok if cond else bad).append((name,detail))
# JSON
for n in ('execution_contract.json','source_manifest.json','progress.json','machine_checks.json'):
    try: json.load(open(os.path.join(OUT,n),encoding='utf-8')); chk(n,True)
    except Exception as e: chk(n,False,str(e))
# annotations
anns=sorted(glob.glob(os.path.join(OUT,'annotations','*','annotation.json')))
chk('annotations=5',len(anns)==5,str(len(anns)))
CHECKS=["GS-S01","GS-S02","GS-S03","GS-S04","GS-T01","GS-T02","GS-T03","GS-T04","GS-T05","GS-T06","GS-T07","GS-T08","GS-X01","GS-X02","GS-N01","GS-N02","GS-N03","GS-N04","GS-N05","GS-N06","GS-N07","GS-N08","GS-N09","GS-W01"]
for p in anns:
    a=json.load(open(p,encoding='utf-8')); sid=a['sample_id']
    chk(f'{sid} 24项', sorted(c['check_id'] for c in a['checks'])==sorted(CHECKS))
    actual=hashlib.sha256(open(pdfs[sid],'rb').read()).hexdigest()
    chk(f'{sid} sha', a['sha256']==actual)
    chk(f'{sid} PENDING_REVIEW', a.get('review_status')=='PENDING_REVIEW')
    chk(f'{sid} 未冻结', 'truth_frozen_at' not in a and 'reviewed_by' not in a)
mc=json.load(open(os.path.join(OUT,'machine_checks.json'),encoding='utf-8'))
chk('machine ok', mc.get('ok') is True)
# ledgers
ts=list(csv.DictReader(open(os.path.join(OUT,'target_status.csv'),encoding='utf-8-sig')))
chk('target_status 17',len(ts)==17,str(len(ts)))
valid={'FOUND','NOT_FOUND','INCOMPLETE','PENDING_REVIEW','N/A'}
chk('status values',all(r['status'] in valid for r in ts))
cands=list(csv.DictReader(open(os.path.join(OUT,'candidates.csv'),encoding='utf-8-sig')))
chk('candidates 5',len(cands)==5,str(len(cands)))
log=list(csv.DictReader(open(os.path.join(OUT,'candidate_screen_log.csv'),encoding='utf-8-sig')))
chk('screen log 20',len(log)==20,str(len(log)))
# manifest
m=json.load(open(os.path.join(OUT,'source_manifest.json'),encoding='utf-8'))
chk('gold 33/33',m['gold']['frozen_match']==33,str(m['gold']['frozen_match']))
chk('screened 20',m['pools']['screened_unique_docs']==20,str(m['pools']['screened_unique_docs']))
# budget
for n in ('budget/GOLD_STANDARD_SPEC_BUDGET_DRAFT_20261103.md','budget/sample_list.csv','budget/structure_pilot.md','budget_local_scan.csv'):
    chk(n,os.path.exists(os.path.join(OUT,n)))
# report/review
for n in ('B1_report.md','review_package/PENDING_REVIEW.md','system_compare/NOT_RUN.md','system_compare/control_results.csv','system_compare/target_candidates.csv','system_issue_leads.csv','commands.log'):
    chk(n,os.path.exists(os.path.join(OUT,n)))
res={'ok':not bad,'passed':len(ok),'failed':bad}
json.dump(res,open(os.path.join(OUT,'selfcheck.json'),'w',encoding='utf-8'),ensure_ascii=False,indent=2)
print(json.dumps(res,ensure_ascii=False,indent=1)[:4000])
