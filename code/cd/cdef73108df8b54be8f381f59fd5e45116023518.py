import json
from pathlib import Path
from audit_current import audit
ROOT=Path(__file__).resolve().parents[2]
BASE=Path(__file__).resolve().parent
items=json.loads((ROOT/'outputs/recognition_completion_20261013/inventory.json').read_text())
results=[]
for item in items:
 try:
  r=audit(Path(item['source_pdf']),BASE/'original_regression'/item['job_id'])
  r['job_id']=item['job_id']
  print(item['filename'],r['pages'],sum(len(r.get(k,[])) for k in ['source_errors','native_blank_source_errors','native_dimension_source_errors','disclosure_source_errors']),flush=True)
 except Exception as e:r={'job_id':item['job_id'],'error':str(e)};print(r,flush=True)
 results.append(r)
 (BASE/'original_regression_results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
