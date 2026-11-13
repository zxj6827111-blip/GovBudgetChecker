import json,sys
from pathlib import Path
ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker');sys.path.insert(0,str(ROOT));OUT=ROOT/'outputs/pdf_acceptance_shanghai2022_20261021';records=[]
for p in (OUT/'runtime_native').glob('*/immutable_snapshot.json'):
 if p.parent.name=='native_correction':continue
 s=json.loads(p.read_text());status=json.loads((p.parent/'status.json').read_text());export=json.loads((p.parent/'report.json').read_text());meta=status['result']['meta'];effective=s['effective_context'];uids={f['fact_uid']for f in effective['facts']};errors=[]
 for binding in meta.get('structured_consumption',[]):
  if binding.get('fact_version')!=effective['version_token']:errors.append({'type':'rule_version','binding':binding})
  if any(u not in uids for u in binding.get('fact_uids',[])):errors.append({'type':'rule_fact_uid','binding':binding})
 for receipt in meta.get('check_receipts',[]):
  if any(u not in uids for u in receipt.get('operand_fact_ids',[])):errors.append({'type':'receipt_operand_uid','rule':receipt['rule_id']})
 for label,value,expected in [('status_revision',status.get('analysis_revision'),s['revision_id']),('rule_metadata_snapshot',meta['fact_snapshot']['snapshot_hash'],s['snapshot_hash']),('export_revision',export.get('analysis_revision'),s['revision_id']),('export_snapshot',export['review_report']['metadata']['fact_snapshot']['snapshot_hash'],s['snapshot_hash'])]:
  if value!=expected:errors.append({'type':label,'observed':value,'expected':expected})
 records.append({'case':p.parent.name,'source_sha256':s['source_file_hash'],'revision_id':s['revision_id'],'snapshot_hash':s['snapshot_hash'],'structured_rule_consumptions':len(meta.get('structured_consumption',[])),'legacy_document_rules_still_present':any(x.get('evaluation_basis')=='legacy_document'for x in meta.get('check_receipts',[])),'errors':errors,'passed':not errors})
(OUT/'rule_query_export_bindings.json').write_text(json.dumps({'documents':records,'passed':bool(records)and all(x['passed']for x in records),'scope':'same source/version binding; not proof of business rule correctness'},ensure_ascii=False,indent=2))
from src.engine.rule_registry import _registrations
special={r.rule_id:{'registered_report_kinds':r.report_kinds,'current_material_executions':0,'status':'not_covered_by_budget_only_batch'} for r in _registrations()if r.rule_id in ['V33-005','V33-222','V33-240']}
(OUT/'specific_rule_coverage.json').write_text(json.dumps(special,ensure_ascii=False,indent=2))
print('binding_documents',len(records),'errors',sum(len(x['errors'])for x in records))
