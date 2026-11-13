from pathlib import Path
import datetime,hashlib,json,re,sys,zipfile
import xml.etree.ElementTree as ET
import pymupdf
ROOT=Path(__file__).resolve().parents[2];OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from scripts.run_zc07_acceptance import _evaluate_export_agreement
from scripts.prepare_independent_acceptance import _validate_technical_preflight

def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
checks={'scope':'R17 PLAN-directed development self-check; no business blind acceptance','verified_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
assert re.search(r'2803 passed, 142 skipped', (OUT/'pytest_final_delivery.log').read_text())
assert re.search(r'140 passed, 2805 deselected', (OUT/'pytest_postgres_final_delivery.log').read_text())
assert read(OUT/'postgres_test_final_delivery_receipt.json')['cleanup']=='complete'
checks['backend']={'passed':2803,'skipped_without_real_pg':142,'failed':0,'additional_real_postgres_passed':140,'remaining_skipped_after_real_pg':2,'ordinary_and_pg_executed_total':2943}
for p in ['ruff_final_delivery.log','dependency_check.log','environment_check.log','ocr_dependency_check.log']:
 expected={'ruff_final_delivery.log':'All checks passed!','dependency_check.log':'All installed packages are compatible','environment_check.log':'环境变量三方对账通过','ocr_dependency_check.log':'No broken requirements found'}[p]
 assert expected in (OUT/p).read_text(),p
assert read(OUT/'log_message_safety_final_delivery.json')['violation_count']==0
assert (OUT/'diff_check_final_delivery.log').read_text()==''
linux_lines=[json.loads(x) for x in (OUT/'linux_native_after.log').read_text().splitlines() if x.startswith('{')]
assert linux_lines[-1]['passed'] and linux_lines[-1]['allocation_blocked'] and linux_lines[-1]['remaining_children']==0
cv_lines=[json.loads(x) for x in (OUT/'cv2_route_probe_after.log').read_text().splitlines() if x.startswith('{')]
assert cv_lines[-1]['passed'] and cv_lines[-1]['scope']=='exact_cv2_multipart_contract_test_with_fixture_predictor_not_model_accuracy'
checks['platform_specific_verification']={'linux_memory':linux_lines[-1],'opencv_protocol':cv_lines[-1]}
assert read(OUT/'source_label_actual_export_check.json')['passed']
checks['actual_pdf_word_source_labels']='passed; actual current exports distinguish authenticated source and unbound explanation'
checks['static_dependency_environment_logs']='passed'
manifest=read(OUT/'independent_intake_final_delivery/code_manifest.json')
changed=[n for n,v in manifest.items() if not (ROOT/n).is_file() or sha(ROOT/n)!=v]
assert not changed,changed
checks['final_frozen_source']={'file_count':len(manifest),'changed_files':changed,'manifest_sha256':sha(OUT/'independent_intake_final_delivery/code_manifest.json')}
assert not any(item['changed_artifacts'] for item in read(OUT/'prior_round_integrity_final_delivery.json'))
checks['prior_round_evidence']=read(OUT/'prior_round_integrity_final_delivery.json')
assert read(OUT/'browser_receipt_final_delivery.json')['status']=='passed'
r=(OUT/'browser_result_final_delivery.log').read_text();browser=json.loads(r[r.index('{'):]);assert browser['stats']['unexpected']==browser['stats']['flaky']==0
assert browser['stats']['expected']==70
checks['frontend_browser']={'passed':70,'failed':0,'retries':0,'scope':'production frontend; API responses intercepted in independent browser fixtures; not human UAT','not_applicable_fixture':'production-disabled /e2e/batch-upload demo CSP test'}
assert 'Compiled successfully' in (OUT/'frontend_build_final_delivery.log').read_text()
assert 'Generating static pages' in (OUT/'frontend_build_final_delivery.log').read_text()
assert 'uiAdapters dual-mode tests passed' in (OUT/'frontend_unit_final_delivery.log').read_text()
assert 'reviewWorkbenchContract.test.ts' in (OUT/'frontend_unit_final_delivery.log').read_text()
assert (OUT/'frontend_unit_final_delivery.log').read_text().count('> jiti tests/')==23
assert 'reviewObligationAdapters.test.ts passed' in (OUT/'frontend_unit_final_delivery.log').read_text()
assert 'error' not in (OUT/'frontend_types_final_delivery.log').read_text().lower()
assert 'Error:' not in (OUT/'frontend_lint_final_delivery.log').read_text()
checks['frontend_frozen_file_count']=sum(name.startswith(('app/','e2e/')) for name in manifest)
checks['frontend_build_lint_unit']='passed on latest source and production browser fixtures'
receipt=read(OUT/'final_delivery_services/service_receipt.json');assert receipt['status']=='passed' and receipt['cleanup']=='complete'
assert receipt['source_unchanged_during_run'] is True
assert all(manifest[n]==v for n,v in receipt['code_fingerprints'].items())
assert len(receipt['scenarios'])==receipt['expected_scenario_count']==27
views=[]
for scene in receipt['scenarios']:
 assert scene['service_validation_passed'] and scene['structured_ingest_validation']['passed'],scene['scenario']
 assert all(scene['repeated_downloads_identical'].values())
 for name,folder,recorded in [('initial',OUT/'final_delivery_services'/scene['scenario'],scene['exports'])]+([('corrected',OUT/'final_delivery_services'/scene['scenario']/'corrected',scene['correction']['exports'])] if 'correction' in scene else []):
  paths=[folder/('report.'+x) for x in ['json','csv','docx','pdf']]
  assert all(sha(p)==recorded[p.name] for p in paths)
  result=_evaluate_export_agreement({'documents':[{'doc_id':scene['job_id'],'export_paths':[str(p) for p in paths]}]},ROOT)
  assert result['export_agreement_rate']==1,(scene['scenario'],name,result)
  views.append({'scenario':scene['scenario'],'view':name,'recorded_SHA_matched':True,**result})
 if 'correction' in scene:
  c=scene['correction'];assert scene['correction_validation_passed'] and c['cache_readback_validation_passed'] and c['original_parse_basis_preserved'] and all(c['history_identical'].values())
write(OUT/'export_self_check_final_delivery.json',views)
scan=next(s for s in receipt['scenarios'] if s['scenario']=='original_scanned_budget')
assert scan['source_sha256']=='5978b569d1f4bf58d07281db4150e72b9cf867fa45a051da4976aabdc6a5eec7'
assert scan['ocr']['requested_pages']==list(range(1,22))
assert [p['page'] for p in scan['ocr']['pages'] if p['ok']]==list(range(1,22)) and not scan['ocr']['failed_pages']
assert scan['pipeline_budget']['total_seconds']==3120 and 0<scan['pipeline_budget']['elapsed_seconds']<3120
scan_dir=OUT/'final_delivery_services/original_scanned_budget';s=read(scan_dir/'status.json');meta=s['result']['meta']
assert not any('缺失预算表:' in row['message'] for row in s['result']['issues']['all'])
scan_context=read(scan_dir/'fact_context.json')
usable=[f for f in scan_context['facts'] if f.get('recognition_status') not in ['low_confidence','rejected']]
assert usable, 'Original complete scan has no eligible amount facts'
for f in usable:
    refs=f.get('extra_dims',{}).get('evidence_refs',[])
    assert refs and all(r.get('file_hash')==scan['source_sha256'] and isinstance(r.get('physical_page'),int) and 1<=r['physical_page']<=21 for r in refs),f['fact_uid']
assert not [f for f in scan_context['facts'] if any(x in (f.get('subject_name') or '') for x in ['栏次','行次'])]
for suffix in ['pdf','docx']:
 if suffix=='pdf':
  with pymupdf.open(scan_dir/'report.pdf') as doc: text='\n'.join(p.get_text() for p in doc)
 else:
  with zipfile.ZipFile(scan_dir/'report.docx') as doc:text=''.join(ET.fromstring(doc.read('word/document.xml')).itertext())
 assert '缺失预算表' not in text,suffix
mixed=next(s for s in receipt['scenarios'] if s['scenario']=='original_mixed_final')
assert float(mixed['correction']['corrected_raw_value'])==float(mixed['correction']['original_raw_value'])+10
facts=read(OUT/'final_delivery_services/original_mixed_final/fact_context.json')['facts']
assert not [f for f in facts if any(x in (f.get('subject_name') or '') for x in ['栏次','行次'])]
checks['services']={'scenarios_passed':27,'elapsed_seconds':receipt['elapsed_seconds'],'source_subset_fingerprints_match_final_freeze':True,'scan_pages':21,'scan_elapsed_seconds':scan['pipeline_budget']['elapsed_seconds'],'scan_total_budget_seconds':3120,'scan_formal_issue_total':meta['evidence_completeness']['formal_issue_total'],'scan_eligible_amount_facts':len(usable),'scan_analysis_conclusion':s.get('analysis_conclusion') or meta.get('analysis_conclusion'),'original_mixed_amount_before':mixed['correction']['original_raw_value'],'original_mixed_projection_after':mixed['correction']['corrected_raw_value'],'exports_independently_reopened_views':len(views),'exports_four_format_agreement':1.0,'cleanup':receipt['cleanup']}
# Derive current query worksheets from original captured DB rows; projected value
# comes only from the copied current FactContext. No signer/truth is generated.
pre=OUT/'technical_preflight_final_delivery';pre.mkdir(exist_ok=True)
def ref(p):return {'path':str(p.resolve()),'sha256':sha(p)}
cache={};context=read(OUT/'final_delivery_services/original_mixed_final/corrected/fact_context.json');target=mixed['correction']['target_id'];current=next(f for f in context['facts'] if f['fact_uid']==target)
for stage in ['initial','corrected']:
 status_path=OUT/'final_delivery_services/original_mixed_final'/('status.json' if stage=='initial' else 'corrected/status.json');status=read(status_path)
 raw=mixed['database'] if stage=='initial' else mixed['correction']['database_after']
 raw_path=pre/(stage+'_database_raw.json');write(raw_path,raw)
 db={'schema_version':'gbc.current-ingest-query.v1','job_id':status['job_id'],'analysis_revision':status['analysis_revision'],'parse_run_id':raw['parse_run']['id'],'document_version_id':raw['parse_run']['document_version_id'],'source_sha256':status['checksum'],'query_timestamp':raw['observed_at'],'facts_count':raw['run_facts_count'],'evidence_count':raw['run_evidence_count'],'corrected_fact_rows':[]}
 if stage=='corrected': db.update(original_database_rows=ref(raw_path),fact_context=ref(OUT/'final_delivery_services/original_mixed_final/corrected/fact_context.json'),correction_ledger=ref(OUT/'final_delivery_services/original_mixed_final/corrected/corrections.jsonl'),corrected_fact_rows=[{'fact_uid':target,'raw_value':current['raw_value'],'evidence_ids':current['evidence_ids']}])
 db_path=pre/(stage+'_database_query.json');write(db_path,db);cache[stage+'_database']=ref(db_path);cache[stage+'_result']=ref(status_path)
readback=mixed['correction']['structured_ingest_validation']['parse_cache'];readback_path=pre/'cache_readback.json';write(readback_path,{'cache_hit':readback['hit'],'cell_provenance_decoded':readback['readback_completed'],'parse_run_id':mixed['correction']['structured_ingest_validation']['parse_run_id'],'raw_parse_cache':readback})
cache.update(source_pdf=ref(OUT/'final_delivery_services/original_mixed_final/source.pdf'),target_fact_uid=target,expected_corrected_raw_value=current['raw_value'],cache_readback=ref(readback_path))
payload={'schema_version':'gbc.independent-technical-preflight.v1','scope':'development technical attachment closure only; official materials reused from previous rounds; no independent human signoff or blind truth','service_receipt':ref(OUT/'final_delivery_services/service_receipt.json'),'original_scanned_budget':{'official_original':True,'synthetic':False,'derived':False,'source_pdf':ref(scan_dir/'source.pdf'),'page_count':21,'processed_physical_pages':list(range(1,22)),'worker_budget_seconds':3120,'elapsed_seconds':scan['pipeline_budget']['elapsed_seconds'],'result':ref(scan_dir/'status.json'),'worker_trace':ref(OUT/'final_delivery_services/worker.log')},'mixed_final_cache':cache}
write(pre/'technical_preflight.development.json',payload);errors=[]
def attach(r,label,read=True):
 if not isinstance(r,dict):errors.append(label+': missing attachment');return {}
 p=Path(r['path']);assert sha(p)==r['sha256'],label
 return json.loads(p.read_text()) if read else {'resolved_path':str(p)}
def require(b,label):
 if not b:errors.append(label)
def timestamp(v,label):
 try:
  d=datetime.datetime.fromisoformat(v.replace('Z','+00:00'));require(d.tzinfo is not None,label+': timezone missing')
 except Exception:require(False,label+': invalid timestamp')
_validate_technical_preflight(payload,attach,require,timestamp)
write(pre/'technical_preflight_check.json',{'scope':'production technical prerequisite evaluator against current actual service attachments; not entire human handoff','passed':not errors,'errors':errors,'business_acceptance':'not_evaluated','sealed_truth_read':False})
assert not errors,errors
checks['current_real_technical_preflight']='passed with immutable DB originals, original JSONL ledger and current FactContext projection'
# Publish only the measured technical prerequisite; human signatures stay empty.
pack=OUT/'independent_intake_final_delivery'
handoff=read(pack/'handoff.pending.json')
handoff['technical_preflight']=ref(pre/'technical_preflight.development.json')
write(pack/'handoff.pending.json',handoff)
from scripts.prepare_independent_acceptance import validate_handoff
gate=validate_handoff(pack/'handoff.pending.json',stage='intake')
write(OUT/'independent_handoff_gate_final_delivery.json',gate)
prep=read(pack/'preparation_receipt.json')
assert prep['actual_frozen_documents']==prep['actual_human_defect_groups']==0 and not gate['passed'] and gate['sealed_truth_read'] is False
checks['business_acceptance']={'status':'pending_not_passed','actual_frozen_documents':0,'actual_human_defect_groups':0,'business_metrics':None,'sealed_truth_read':False}
corpus=read(OUT/'shanghai/final_delivery/final_development_audit.json')
assert corpus['complete'] and corpus['completed_documents']==20 and corpus['total_pages']==678 and corpus['technical_integrity_passed']
for obs in read(OUT/'shanghai/metadata_observation.json'):
 assert sha(Path('/Users/zhangxingjin/Desktop/决算测试上海市')/obs['filename'])==obs['sha256']
assert all(d['organization'] for d in corpus['documents'])
reviews=read(OUT/'shanghai/final_delivery/manual_review.pending.json')
obligations=read(OUT/'shanghai/final_delivery/obligations_review.pending.json')
assert len(obligations)==860 and all(row['reviewer_id'] is None and row['human_complete'] is None for row in obligations)
assert all(row['reviewer_id'] is None and row['human_decision'] is None for row in reviews)
assert sum(row['system_status']=='completed' for row in obligations)==543
assert sum(row['system_status']=='ai_not_run' for row in obligations)==20
assert sum(row['system_status']=='insufficient_data' for row in obligations)==297
checks['real_shanghai_development']={'documents':20,'pages':678,'source_files_unchanged':True,'AI_semantic_not_run':20,'deterministic_insufficient_data':297,'actual_human_decisions':0,
 'integrity_errors':corpus['errors'],'candidate_issues':sum(d['issue_count'] for d in corpus['documents']),
 'unverified_high_severity_candidates':len(corpus['manual_evidence_risks']),
 'completed_obligations':sum(d['completed'] for d in corpus['documents']),
 'applicable_obligations':sum(d['applicable'] for d in corpus['documents']),
 'automatically_unresolved_organization':sum(d['organization'] is None for d in corpus['documents']),
 'business_verified':False}
extended=receipt['extended_validation'];assert extended['passed']
assert extended['browser_live']['passed']
assert extended['review_control']['persisted_and_still_blocked']
recovery=extended['recovery']
assert recovery['database_all_tables_identical'] and recovery['uploads_all_files_identical'] and recovery['audit_identical'] and recovery['application_state_identical']
assert recovery['restarted_login_organization_and_exports_passed'] and recovery['restarted_verified_exports']==4
checks['real_browser_csv_review_recovery']={'passed':True,'scope':'actual automated production browser against real API/PG; not human visual/business signoff','recovery':recovery}
checks['ocr_runtime_cleanup']=read(OUT/'ocr_runtime.json')['cleanup'];assert checks['ocr_runtime_cleanup']['status'] in ['complete','active_for_followup']
checks['status']='passed_for_remediation_development_scope; entire_PLAN_not_accepted'
write(OUT/'self_check_final_delivery.json',checks);print(json.dumps(checks,ensure_ascii=False,indent=2))
