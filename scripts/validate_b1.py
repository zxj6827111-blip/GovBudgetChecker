# -*- coding: utf-8 -*-
import csv, glob, hashlib, json, os, re, sys
OUT=sys.argv[1]; PDFS=sys.argv[2]
pdfs=json.load(open(PDFS,encoding='utf-8'))
ok=[]; bad=[]
def chk(n,c,d=''):
    (ok if c else bad).append((n,d))
CHECKS=["GS-S01","GS-S02","GS-S03","GS-S04","GS-T01","GS-T02","GS-T03","GS-T04","GS-T05","GS-T06","GS-T07","GS-T08","GS-X01","GS-X02","GS-N01","GS-N02","GS-N03","GS-N04","GS-N05","GS-N06","GS-N07","GS-N08","GS-N09","GS-W01"]
for n in ('execution_contract.json','source_manifest.json','progress.json','machine_checks.json','machine_checks_full.json','decimal_checks.csv','budget_ledger.csv','target_evidence.csv','family_budget.csv','checksum_audit.csv','b1_pdfs.json','pipeline_inputs.csv','blind_annotation_context/model_runs.json','blind_annotation_context/input_sha256.json'):
    chk(n,os.path.exists(os.path.join(OUT,n)))
anns=sorted(glob.glob(os.path.join(OUT,'annotations','*','annotation.json')))
chk('annotations=5',len(anns)==5,str(len(anns)))
for p in anns:
    a=json.load(open(p,encoding='utf-8')); sid=a['sample_id']
    chk(f'{sid} 24',sorted(c['check_id'] for c in a['checks'])==sorted(CHECKS))
    actual=hashlib.sha256(open(pdfs[sid],'rb').read()).hexdigest()
    chk(f'{sid} sha',a['sha256']==actual)
    chk(f'{sid} pending',a.get('review_status')=='PENDING_REVIEW')
    chk(f'{sid} no freeze','truth_frozen_at' not in a and 'reviewed_by' not in a)
    chk(f'{sid} no CD',all(c['verdict']!='cannot_determine' for c in a['checks']))
    chk(f'{sid} 24 pass/defect',all(c['verdict'] in ('pass','defect') for c in a['checks']))
mc=json.load(open(os.path.join(OUT,'machine_checks_full.json'),encoding='utf-8'))
chk('machine_full ok',mc.get('ok') is True)
dc=list(csv.DictReader(open(os.path.join(OUT,'decimal_checks.csv'),encoding='utf-8-sig')))
chk('decimal 16 PASS',len(dc)==16 and all(r['result']=='PASS' for r in dc),str(len(dc)))
ts=list(csv.DictReader(open(os.path.join(OUT,'target_status.csv'),encoding='utf-8-sig')))
chk('targets 17',len(ts)==17,str(len(ts)))
st={r['target_id']:r['status'] for r in ts}
chk('T02 PENDING',st.get('T02')=='PENDING_REVIEW')
chk('T03 NOT_FOUND',st.get('T03')=='NOT_FOUND')
chk('T05 PENDING',st.get('T05')=='PENDING_REVIEW')
chk('T07 PENDING',st.get('T07')=='PENDING_REVIEW')
chk('N02 PENDING',st.get('N02')=='PENDING_REVIEW')
chk('N06 PENDING',st.get('N06')=='PENDING_REVIEW')
chk('controls INCOMPLETE',all(r['status']=='INCOMPLETE' for r in ts if r['target_type']=='control'))
cands=list(csv.DictReader(open(os.path.join(OUT,'candidates.csv'),encoding='utf-8-sig')))
chk('candidates 5',len(cands)==5,str(len(cands)))
log=list(csv.DictReader(open(os.path.join(OUT,'candidate_screen_log.csv'),encoding='utf-8-sig')))
chk('screen 20',len(log)==20,str(len(log)))
m=json.load(open(os.path.join(OUT,'source_manifest.json'),encoding='utf-8'))
chk('gold 33',m['gold']['frozen_match']==33,str(m['gold']['frozen_match']))
chk('screened 20',m['pools']['screened_unique_docs']==20,str(m['pools']['screened_unique_docs']))
# manifest files hashes (excluding self)
bad_files=[]
for f in m['files']:
    p=os.path.join(OUT,f['path'])
    if f['path']=='source_manifest.json': continue
    if not os.path.exists(p) or hashlib.sha256(open(p,'rb').read()).hexdigest()!=f['sha256']: bad_files.append(f['path'])
chk('manifest hashes',not bad_files,str(bad_files[:5]))
ca=list(csv.DictReader(open(os.path.join(OUT,'checksum_audit.csv'),encoding='utf-8-sig')))
chk('checksum 20/20',len(ca)==20 and all(r['normalized_64']=='True' for r in ca))
chk('checksum 19 CR',sum(1 for r in ca if r['trailing_cr']=='True')==19)
mr=json.load(open(os.path.join(OUT,'blind_annotation_context/model_runs.json'),encoding='utf-8'))
chk('model runs 5',len(mr['runs'])==5)
for n in ('B1_report.md','review_response.md','review_package/PENDING_REVIEW.md','system_compare/NOT_RUN.md','system_compare/control_results.csv','system_compare/target_candidates.csv','system_issue_leads.csv','commands.log','budget/GOLD_STANDARD_SPEC_BUDGET_DRAFT_20261103.md','budget/sample_list.csv','budget/structure_pilot.md','budget_local_scan.csv'):
    chk(n,os.path.exists(os.path.join(OUT,n)))
res={'ok':not bad,'passed':len(ok),'failed':bad}
json.dump(res,open(os.path.join(OUT,'selfcheck.json'),'w',encoding='utf-8'),ensure_ascii=False,indent=2)
print(json.dumps(res,ensure_ascii=False,indent=1)[:4000])
