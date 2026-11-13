import ast
import collections
import gzip
import hashlib
import json
import pathlib
import subprocess

ROOT=pathlib.Path(__file__).resolve().parents[1]
CAND=pathlib.Path('/Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106')
EV=CAND/'outputs/unified_remediation_20261106'
OLD=pathlib.Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs/real_painpoint_effect_closeout_20261105/candidate_d1_final')
FROZEN=pathlib.Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs/pending_review_20261101/acceptance_kit/v1-regression-r4')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):
    with (gzip.open(p,'rt') if p.suffix=='.gz' else p.open()) as f:return json.load(f)
def dump(name,obj):(ROOT/'receipts'/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2))
def git(*args):return subprocess.check_output(['git',*args],cwd=CAND)
summary={}
identity=read(EV/'tool_identity.json')
checks=[{'path':k,'expected':v,'actual':sha(CAND/k),'match':sha(CAND/k)==v} for group in ['files','contracts'] for k,v in identity[group].items()]
summary['identity_hashes_match']=all(c['match'] for c in checks)
summary['head']=git('rev-parse','HEAD').decode().strip()
summary['post_candidate_paths']=git('diff','--name-only','5ac9e71b..HEAD').decode().splitlines()
summary['branch']=git('branch','--show-current').decode().strip()
summary['candidate_status_clean']=not git('status','--porcelain').decode().strip()
summary['frozen_r4_harness_sha256']=sha(FROZEN/'tools/verify_pending_review.py')
summary['saved_harness_sha256']=hashlib.sha256(git('show','3ca22f88:scripts/verify_pending_review.py')).hexdigest()
summary['contracts_equal_frozen']={p.name:sha(p)==sha(FROZEN/p.name) for p in (CAND/'contracts/pending_review_r4').glob('*.json')}
before=ast.parse(git('show','3ca22f88:tests/test_verify_pending_review.py').decode())
after=ast.parse((CAND/'tests/test_verify_pending_review.py').read_text())
def assertions(tree):return collections.Counter(ast.dump(n,include_attributes=False) for n in ast.walk(tree) if isinstance(n,ast.Assert))
summary['saved_assertions']=sum(assertions(before).values())
summary['missing_saved_assertions']=list((assertions(before)-assertions(after)).elements())
summary['saved_test_functions']={ 'before':len([n for n in ast.walk(before) if isinstance(n,ast.FunctionDef) and n.name.startswith('test_')]),
    'after':len([n for n in ast.walk(after) if isinstance(n,ast.FunctionDef) and n.name.startswith('test_')])}
manifest=read(EV/'review_manifest.json');bad=[]
for e in manifest['files']:
    p=pathlib.Path(e['path'])
    if not p.exists() or sha(p)!=e['sha256']:bad.append(str(p))
summary['delivery_manifest_mismatches']=bad
code_drift={}
for label in ['baseline_main','final_d1','final_d2','cover_r2_base','cover_r2_d1','cover_r2_d2']:
    meta=read(EV/label/'meta.json'); drift=[]
    for path,expected in meta['code']['files'].items():
        if path.split('/')[0] in {'src','api','config','rules','gbc_yaml','ocr_service','contracts'}:
            target='47581360' if label in {'baseline_main','cover_r2_base'} else '5ac9e71b'
            actual=hashlib.sha256(git('show',target+':'+path)).hexdigest()
            if actual!=expected:drift.append(path)
    code_drift[label]={'business_drift_paths':drift,'code_digest':meta['code']['digest'],
                       'profile':{k:v for k,v in meta['profile'].items() if k in ['AI_ASSIST_ENABLED','OCR_ENABLED','GBC_TEMPLATE_SCOPE','GBC_BUDGET_TEMPLATE_SCOPE']},
                       'meta_sha256':sha(EV/label/'meta.json'),'kit_digest':meta['kit_digest']}
summary['collection_code_bindings']=code_drift
layers=['issues','candidate_findings','tips','manual_review'];counts=collections.Counter();adapter=[]
for p in sorted((OLD/'status').glob('*.status.json.gz')):
    sid=p.name.removesuffix('.status.json.gz');raw=read(p)['review_report'];new=read(EV/'adapted160_final/records'/f'{sid}.json.gz')
    diffs=[]
    for field,layer in zip(layers,['formal','candidate','tips','manual_review']):
        counts[field]+=len(raw[field])
        if raw[field]!=new['finding_layers'][layer]:diffs.append(field)
    copied=EV/'adapted160_final/status'/p.name
    adapter.append({'sample_id':sid,'old_status_sha256':sha(p),'copied_status_sha256':sha(copied),
                    'raw_bytes_preserved':sha(p)==sha(copied),'layer_mismatches':diffs,
                    'formal_items':len(raw['issues']),'adapted_items':len(new['findings'])})
summary['adapter']={'samples':len(adapter),'counts':dict(counts),'mismatches':[r for r in adapter if r['layer_mismatches'] or not r['raw_bytes_preserved']],
    'old_meta_preserved':sha(OLD/'meta.json')==sha(EV/'adapted160_final/meta.json')}
dump('adapter_raw_independent.json',adapter)
references=[]
for debt in read(EV/'ledger.json')['historical_items']['reference_debt_14']:
    sid=debt['sample_id'];raw=read(EV/'final_d1/status'/f'{sid}.status.json.gz')['review_report']
    issues=[i for i in raw['issues'] if i['rule_id']==debt['key'][0] and i.get('page_number')==debt['key'][-1]]
    issue=next((i for i in issues if i.get('check_relation_id') in debt['refs']),None)
    assert issue is not None,(sid,debt)
    loc=issue.get('location') or {};group=loc.get('reference_kind')=='source_diagnostic_group'
    ids=loc.get('relation_ids') if group else [issue['check_relation_id']]
    declared=[v['relation_id'] for v in loc.get('relations',[])] if group else ids
    available={f"{r['rule_id']}:{r['sub_check_id']}":r for r in raw['check_receipts']}
    references.append({'sample_id':sid,'reference':debt['refs'][0], 'issue_id':issue['id'],'page':issue.get('page_number'),
       'preserved':True,'reference_kind':'source_diagnostic_group' if group else 'check', 'member_count':len(ids),
       'group_members_equal':set(ids)==set(declared),'missing':[v for v in ids if v not in available],
       'member_states':dict(collections.Counter(available[v]['status'] for v in ids if v in available)),
       'reverse_refs_complete':all(issue['id'] in available[v].get('issue_refs',[]) for v in ids if v in available),
       'member_receipts_sha256':hashlib.sha256(json.dumps([available[v] for v in ids if v in available],sort_keys=True).encode()).hexdigest()})
summary['references14']={'count':len(references),'closure_failures':[r for r in references if r['missing'] or not r['group_members_equal'] or not r['reverse_refs_complete']]}
dump('references14_independent.json',references)
aggregates=[]
for sid in ['RA-036','RB-2022-U11','RB-2025-D05','RB-2025-U03','RB-2025-U02']:
    pairs={}
    for label in ['baseline_main','final_d1']:
        raw=read(EV/label/'status'/f'{sid}.status.json.gz')
        rows=[r for r in raw['review_report']['check_receipts'] if r['rule_id']=='V33-TREND-COMPLETION-RATE']
        parent=next(r for r in rows if r['sub_check_id'].startswith('OBL-'))
        pairs[label]={'parent':parent,'child_statuses':dict(collections.Counter(r['status'] for r in rows if r is not parent)),
            'coverage':[v for v in raw['check_coverage']['instances'] if v['obligation_id']=='OBL-TREND-COMPLETION-RATE']}
    aggregates.append({'sample_id':sid,**pairs})
dump('aggregates_independent.json',aggregates)
dump('source_identity.json',{'summary':summary,'hash_checks':checks})
print(json.dumps(summary,ensure_ascii=False,indent=2))
