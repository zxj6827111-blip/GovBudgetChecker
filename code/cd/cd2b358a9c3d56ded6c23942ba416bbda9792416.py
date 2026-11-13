"""Read-only recomparison of frozen reviewer status; writes only this audit directory."""
import gzip
import hashlib
import json
from pathlib import Path
import remaining_boundary_probes as controls

e=controls.e
ROOT=controls.ROOT
OUT=Path(__file__).resolve().parent
REV=ROOT/'outputs/r22_final_independent_review_codex_20261107'
PKG=ROOT/'outputs/r22_gold33_20261107'
old_hashes=json.loads((REV/'SHA256SUMS.json').read_text())
checks=[]
def read(p):
 rel=str(p.relative_to(REV))
 checks.append({'path':rel,'matches_frozen_reviewer_sha':hashlib.sha256(p.read_bytes()).hexdigest()==old_hashes[rel]})
 return json.loads(gzip.decompress(p.read_bytes()))
def expected(payload,material_hash):
 i=e.extract_payload_identity(payload)
 return {'material':{'checksum_sha256':material_hash},
         'generation':{'analysis_revision':i['generation.analysis_revision']['value']},
         'code_identity':{'rule_registry_version':i['code.rule_registry_version']['value'],
                          'rule_policy_version':i['code.rule_policy_version']['value']},
         'config_identity':{'use_local_rules':i['config.use_local_rules']['value'],
                            'use_ai_assist':i['config.use_ai_assist']['value'],'mode':i['config.mode']['value']}}
all_rows=[];batch_results=[]
for batch,name,inputdir in [
 ('gold','gold33_main_final_entry_compare.json',ROOT/'outputs/gold_baseline_20261102/inputs'),
 ('indep','indep_main_final_entry_compare.json',PKG/'indep_inputs')]:
 manifest=json.loads((inputdir/'inputs.json').read_text());anchors={x['sample_id']:x['sha256'] for x in manifest['samples']}
 saved=json.loads((PKG/'entry_compare'/name).read_text());archive=json.loads((PKG/'entry_compare/archive_1df7a5dd'/name).read_text());by_id={x['sample_id']:x for x in saved['verdicts']}
 rows=[]
 for sid in sorted(by_id):
  b=read(REV/(batch+'_main')/'status'/f'{sid}.status.json.gz');a=read(REV/(batch+'_final')/'status'/f'{sid}.status.json.gz')
  c={'contract_version':e.CONTRACT_VERSION,'mode':'cross_generation',
    'expected_before':expected(b,anchors[sid]),'expected_after':expected(a,anchors[sid]),
    'parse_generation_basis':{'generation.parse_run_id':'offline_replay_without_db',
                            'generation.document_version_id':'offline_replay_without_db'}}
  r=e.compare_payloads(b,a,c)
  v={'sample_id':sid,'verdict':r['verdict'],'pending_reason':r.get('pending_reason'),
     'claim_reasons':r.get('claim_reasons',[]),'formal_before':r.get('snapshot_before',{}).get('formal_total_deduped'),
     'formal_after':r.get('snapshot_after',{}).get('formal_total_deduped'),
     'added':r.get('added_identities',[]),'removed':r.get('removed_identities',{'withdrawn_with_evidence':[],'removed_unexplained':[]}),
     'content_changed':r.get('content_changed_identities',[]),'evidence_lost':r.get('evidence_lost',[]),
     'zero_regression_claim':r.get('zero_regression_claim')}
  rows.append(v);all_rows.append(v)
 batch_results.append({'batch':batch,'samples':len(rows),'fresh_matches_saved_all_fields':all(v==by_id[v['sample_id']] for v in rows),
                       'saved_verdicts_equal_archive':saved['verdicts']==archive['verdicts'],
                       'tool_sha_matches_main':saved['tool_identity']==e._tool_identity(),
                       'tool_commit':saved['tool_commit'],'rows':rows})
result={'original_status_files':len(checks),'frozen_raw_hashes_ok':all(c['matches_frozen_reviewer_sha'] for c in checks),
        'compared':sum(x['verdict']=='compared' for x in all_rows),
        'evidence_loss_records':sum(len(x['evidence_lost']) for x in all_rows),
        'batches':batch_results,'raw_hashes':checks}
(OUT/'real39_recomparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k not in ['batches','raw_hashes']},ensure_ascii=False))
for b in batch_results:print(json.dumps({k:v for k,v in b.items() if k!='rows'},ensure_ascii=False))
