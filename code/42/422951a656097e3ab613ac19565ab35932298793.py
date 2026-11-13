from pathlib import Path
import json,importlib.util,collections,hashlib,datetime,urllib.request
B=Path('/Volumes/workSSD/software development/GovBudgetChecker');R=Path(__file__).parent;D=B/'outputs/unified_closeout_round6_20261104'
s=importlib.util.spec_from_file_location('pain_scan_audit',D/'scan_painpoints.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m);m.OUT=R/'painpoint_scan_replay.json';m.main()
old=json.loads((D/'painpoint_scan.json').read_text());new=json.loads(m.OUT.read_text());cands=new['candidates'];proof=[]
for row in cands:
 p=Path(row['status_path']);st=json.loads(p.read_text());meta=json.loads((p.parent/'job_meta.json').read_text());pdfs=list(p.parent.glob('*.pdf'));match=[]
 for f in pdfs:
  digest=hashlib.sha256(f.read_bytes()).hexdigest()
  if digest==row['checksum'].strip():match.append(str(f))
 proof.append({'job_id':row['job_id'],'checksum':row['checksum'],'status_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'material_pdf_matches':match,'unfinished_total':row['unfinished_total'],'stored_unfinished':len(row['unfinished_checks']),'updated_at_type':type(meta.get('updated_at')).__name__,'mode':st.get('mode'),'use_ai_assist':st.get('use_ai_assist'),'ai_execution':st.get('ai_execution') or (st.get('result') or {}).get('meta',{}).get('ai_execution')})
checksum_anomaly=[];timestamp_types=collections.Counter();metadata_codes=collections.Counter()
for p in sorted((B/'uploads').glob('*/job_meta.json')):
 try:meta=json.loads(p.read_text())
 except (ValueError,OSError):continue
 c=str(meta.get('checksum') or '');timestamp_types[type(meta.get('updated_at')).__name__]+=1
 if c and (len(c)!=64 or c.strip()!=c):checksum_anomaly.append({'job':p.parent.name,'raw_length':len(c),'stripped_length':len(c.strip())})
 metadata_codes[str(meta.get('doc_type'))]+=1
safe_config={};config=B/'data/ai-model-settings.json'
if config.exists():
 a=json.loads(config.read_text());active=next((p for p in a.get('profiles',[]) if p.get('id')==a.get('active_profile_id')),{});safe_config={'file_present':True,'enabled':a.get('enabled'),'default_use_ai':a.get('default_use_ai'),'model':active.get('model'),'key_present':bool(active.get('encrypted_api_key')),'configured_models':[p.get('model') for p in a.get('profiles',[])]}
health={}
for port in [9010,9009]:
 try:
  with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=3) as resp:
   raw=json.loads(resp.read());health[str(port)]={'http':resp.status,'status':raw.get('status'),'models_ready':raw.get('models_ready'),'protocol_version':raw.get('protocol_version'),'model_fingerprint':raw.get('model_fingerprint'),'pipeline_fingerprint':raw.get('pipeline_fingerprint')}
 except Exception as exc:health[str(port)]={'error_type':type(exc).__name__,'error':str(exc)}
out={'scan_reproduced':old==new,'totals':{k:new[k] for k in ['scanned_jobs','unique_materials','technical_materials','excluded_gold_or_b1','business_ambiguity_materials','ai_not_run_total']},'candidate_years':dict(collections.Counter(str(c['fiscal_year']) for c in cands)),'candidate_types':dict(collections.Counter(c['doc_type'] for c in cands)),'top10_years':sorted({c['fiscal_year'] for c in cands[:10]}),'top10_types':sorted({c['doc_type'] for c in cands[:10]}),'candidate_proofs':proof,'checksum_anomalies':checksum_anomaly,'metadata_timestamp_types':dict(timestamp_types),'all_metadata_doc_types':dict(metadata_codes),'safe_ai_settings':safe_config,'local_service_health':health,'notes':['technical_materials is post-exclusion and filename-filter','unfinished_checks truncated to first20 by scan','business record_type alone is not adjudication of true ambiguity','health and settings are not a successful inference call']}
(R/'painpoint_inventory_audit.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
print(json.dumps({k:v for k,v in out.items() if k!='candidate_proofs'},ensure_ascii=False,indent=2))
