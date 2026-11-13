import json,hashlib,sys
from pathlib import Path
BASE=Path(__file__).resolve().parent
from audit_current import audit
manifest=json.loads((BASE/'sample_manifest.json').read_text())
assert hashlib.sha256((BASE/'frozen_truth.json').read_bytes()).hexdigest()==(BASE/'truth_sha256.txt').read_text().strip()
results=[]
for item in manifest['selected']:
 try:
  result=audit(Path(item['source_pdf']),BASE/'after'/item['sample_id'])
  cov=json.loads((BASE/'after'/item['sample_id']/'coverage.json').read_text())
  result.update(sample_id=item['sample_id'],category=item['category'],blocking=cov['blocking_total'],blocking_ids=cov['blocking_obligation_ids'])
  assert result['sha256']==item['sha256'] and result['source_unchanged']
  print(json.dumps({'id':item['sample_id'],'pages':result['pages'],'blockers':result['blocking'],'blocked_tables':sum(t['projection']=='blocked' for t in result['tables']),'source_errors':sum(len(result.get(k,[])) for k in ['source_errors','native_blank_source_errors','native_dimension_source_errors','disclosure_source_errors'])},ensure_ascii=False),flush=True)
 except BaseException as error:
  result={'sample_id':item['sample_id'],'filename':item['filename'],'error':type(error).__name__+': '+str(error)}
  print('ERROR '+str(result),flush=True)
 results.append(result)
 (BASE/'results_after.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
