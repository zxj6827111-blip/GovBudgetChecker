import collections
import copy
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
CAND=Path('/Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106')
EV=CAND/'outputs/unified_review_remediation_20261106'
OLD=CAND/'outputs/unified_remediation_20261106'
PRIOR=Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs/unified_independent_review_20261106')
sys.path.insert(0,str(CAND))
from scripts import effect_acceptance_v3 as h,verify_pending_review as gov

def read(p):
    with(gzip.open(p,'rt')if p.suffix=='.gz'else p.open())as f:return json.load(f)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def dump(n,v):(ROOT/'receipts'/n).write_text(json.dumps(v,ensure_ascii=False,sort_keys=True,indent=2))
def git(*args):return subprocess.check_output(['git',*args],cwd=CAND)

identity=read(EV/'source_identity.json')
files=[{'file':n,'expected':v,'actual':sha(CAND/n),'match':v==sha(CAND/n)}for n,v in identity['source_files'].items()]
patch=git('diff','e92ccd6b..69884e9c')
previous_bad=[r['path']for r in read(PRIOR/'review_index.json')['files']if sha(Path(r['path']))!=r['sha256']]
manifest=read(EV/'review_manifest.json');delivery_bad=[]
for r in manifest['files']:
    p=Path(r['path'])
    if not p.exists()or sha(p)!=r['sha256']:delivery_bad.append(r['path'])
meta_rows=[]
for label in ['final_collection','repeat4_collection','repeat_source_view4']:
    meta=read(EV/label/'meta.json');mismatch=[]
    for path,expected in meta['code']['files'].items():
        got=hashlib.sha256(git('show','69884e9c:'+path)).hexdigest()
        if got!=expected:mismatch.append(path)
    actual=list((EV/label/'status').glob('*.status.json.gz'))
    meta_rows.append({'label':label,'samples':len(meta['samples']),'statuses':len(actual),'failed':meta['failed'],
        'code_root_writes':meta['code_root_writes'],'code_drift':meta['code_after']!=meta['code']['digest'],
        'dotenv_isolation_ok':meta['dotenv_isolation_ok'],'tracked_source_mismatches':mismatch,
        'code_digest':meta['code_after'],'kit_digest':meta['kit_digest'],'meta_sha256':sha(EV/label/'meta.json'),
        'retained_full_fact_context_files':sum(1 for p in(EV/label/'work').glob('*/uploads/*/fact_context.json'))})
source={'source_identity_checks':files,'source_changes_patch_matches_git_diff':patch==(EV/'source_changes.patch').read_bytes(),
    'changed_paths':git('diff','--name-only','e92ccd6b..69884e9c').decode().splitlines(),
    'prior_sealed_review_mismatches':previous_bad,'delivery_manifest_mismatches':delivery_bad,'collections':meta_rows,
    'main':git('rev-parse','main').decode().strip(),'candidate_clean':not git('status','--porcelain').decode().strip(),
    'flow_business_source_diff':git('diff','--name-only','632f9252..69884e9c','--','src','api','config','rules','app','contracts').decode().splitlines()}
dump('source_and_collection_identity.json',source)
dump('repeat4_independent.json',h.reproducibility(EV/'repeat_source_view4',EV/'repeat4_collection'))

# 保留/重放旧包；绝不迁移旧授权到当前代际。
cases=[('old27',OLD/'baseline_main',OLD/'final_d1',OLD/'kit',PRIOR/'independent_effect_final_v1.json'),
       ('old_cover',OLD/'cover_r2_base',OLD/'cover_r2_d1',OLD/'kit_cover_bound_r2',PRIOR/'independent_effect_cover_v1.json'),
       ('current_unadjudicated',OLD/'final_d1',EV/'final_collection',EV/'kit_candidate',None)]
effects=[]
for name,b,c,kit,evidence in cases:
    ev=h.load_evidence(evidence,sha(evidence))if evidence else{}
    report=h.compare_collections(b,c,kit,ev)
    dump(name+'_independent_replay.json',report)
    effects.append({'case':name,'overall':report['overall'],'samples':report.get('samples'),
                    'problems':len(report['problems']),'pending_objects':len(report['pending'])})
dump('effect_replay_counts.json',effects)

# 31条范围自检只是范围/内部一致性，并不是业务授权。
scope=PRIOR/'independent_scope31_v1.json'
g=gov.compare_runs(EV/'final_collection',EV/'final_collection','self',EV/'kit_candidate',
                  scope_review=scope,scope_review_sha256=sha(scope))
dump('scope31_governance_independent_replay.json',g)

# 原件事实未变化的完整身份/金额投影只核对保存了全文事实的三个真实上传材料。
before_paths={'SP-ORIG-07':OLD/'cover_r2_base/work/SP-ORIG-07/uploads/SP-ORIG-07/fact_context.json',
    'RB-2023-U04':OLD/'baseline_main/work/RB-2023-U04/uploads/RB-2023-U04/fact_context.json',
    'RB-2025-U02':OLD/'baseline_main/work/RB-2025-U02/uploads/RB-2025-U02/fact_context.json'}
orders={'SP-ORIG-07':7,'RB-2023-U04':101,'RB-2025-U02':102}
keys=['org_name','org_scope','fiscal_year','table_code','indicator','fund_scope','period','budget_stage','subject_code',
      'subject_name','row_order','raw_value','normalized_value','unit','recognition_status']
rows=[]
for sid,path in before_paths.items():
    new=EV/'flow/fact_contexts'/f'{orders[sid]}.json';a,b=read(path)['facts'],read(new)['facts']
    project=lambda rs:collections.Counter(json.dumps({k:r.get(k)for k in keys},sort_keys=True,ensure_ascii=False)for r in rs)
    rows.append({'sample_id':sid,'before_count':len(a),'after_count':len(b),'identity_value_projection_equal':project(a)==project(b),
                 'before_sha256':sha(path),'after_sha256':sha(new)})
dump('monetary_projection3_independent.json',{'scope':'three saved real-upload contexts only, not all27','projection_fields':keys,'rows':rows})

print(json.dumps({'source_files_match':all(r['match']for r in files),'patch_matches':source['source_changes_patch_matches_git_diff'],
   'prior_seals_preserved':not previous_bad,'delivery_preserved':not delivery_bad,'collections':meta_rows,
   'effects':effects,'scope_self':g['overall'],'amount_projections_equal':sum(r['identity_value_projection_equal']for r in rows)},ensure_ascii=False))
