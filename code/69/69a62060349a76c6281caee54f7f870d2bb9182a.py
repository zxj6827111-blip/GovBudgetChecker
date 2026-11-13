import json,sys,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from audit_no_fact_context import audit
BASE=Path(__file__).resolve().parent
manifest=json.loads((BASE/'holdout_manifest.json').read_text())
assert hashlib.sha256((BASE/'holdout_frozen_truth.json').read_bytes()).hexdigest()==(BASE/'holdout_truth_sha256.txt').read_text().strip()
results=[]
for item in manifest['selected']:
 result=audit(Path(item['source_pdf']),BASE/'holdout_after'/item['sample_id'])
 cov=json.loads((BASE/'holdout_after'/item['sample_id']/'coverage.json').read_text())
 result.update(sample_id=item['sample_id'],blocking=cov['blocking_total'],blocking_ids=cov['blocking_obligation_ids'])
 assert result['sha256']==item['sha256'] and result['source_unchanged']
 results.append(result)
 print(json.dumps({'id':item['sample_id'],'blocking':result['blocking'],'projection_errors':sum(t['projection']=='blocked' for t in result['tables']),'source_errors':len(result['source_errors'])},ensure_ascii=False),flush=True)
 (BASE/'holdout_results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
