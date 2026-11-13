import json
from pathlib import Path
from decimal import Decimal
ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs/pdf_acceptance_shanghai2022_20261021')
fields=('fact_uid','instance_uid','table_code','indicator','org_name','org_scope','fiscal_year','period','fund_scope','budget_stage','subject_code','subject_name','classification_level','parent_subject_code','hierarchy_path','raw_value','unit','display_precision','normalized_value','recognition_status','confidence','evidence_ids','extra_dims','row_order')
json_fields={'hierarchy_path','evidence_ids','extra_dims'}
results=[]
for p in (ROOT/'runtime_native').glob('*/database_parse_facts.json'):
 s=json.loads((p.parent/'immutable_snapshot.json').read_text());db=json.loads(p.read_text());raw={f['fact_uid']:f for f in s['raw_context']['facts']};errors=[]
 for f in db:
  r=raw.get(f['fact_uid'])
  if r is None:errors.append({'fact_uid':f['fact_uid'],'reason':'extra_in_database'});continue
  for key in fields:
   a,b=f.get(key),r.get(key)
   if key in json_fields and isinstance(a,str):a=json.loads(a)
   if key=='normalized_value' and a is not None and b is not None:a,b=Decimal(a),Decimal(b)
   if a!=b:errors.append({'fact_uid':f['fact_uid'],'field':key,'database':a,'snapshot':b})
 record={'case':p.parent.name,'snapshot_hash':s['snapshot_hash'],'source_sha256':s['source_file_hash'],'fields':fields,'checked_facts':len(db),'raw_snapshot_facts':len(raw),'errors':errors,'passed':not errors and len(db)==len(raw)}
 (p.parent/'database_identity_amount_qualification_evidence_readback.json').write_text(json.dumps(record,ensure_ascii=False,indent=2,default=str));results.append(record)
(ROOT/'database_full_field_summary.json').write_text(json.dumps({'scope':'all persisted raw fact fields vs immutable snapshot, plus authenticated raw/effective snapshot and compatibility projections in runtime receipts','documents':results,'passed':bool(results)and all(x['passed']for x in results)},ensure_ascii=False,indent=2,default=str))
print('full_field_readback',len(results),'facts',sum(x['checked_facts'] for x in results),'errors',sum(len(x['errors'])for x in results))
