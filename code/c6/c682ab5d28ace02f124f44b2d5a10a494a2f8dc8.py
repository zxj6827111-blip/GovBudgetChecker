import json,sys,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from audit_no_fact_context import audit
BASE=Path(__file__).resolve().parent
manifest=json.loads((ROOT/'outputs/putuo_random_check_20261014/sample_manifest.json').read_text())
results=[]
for item in manifest['selected']:
 result=audit(Path(item['source_pdf']),BASE/'after'/item['sample_id'])
 cov=json.loads((BASE/'after'/item['sample_id']/'coverage.json').read_text())
 result.update(sample_id=item['sample_id'],blocking=cov['blocking_total'],blocking_ids=cov['blocking_obligation_ids'])
 assert result['sha256']==item['sha256'] and result['source_unchanged']
 results.append(result)
 print(json.dumps({'id':item['sample_id'],'blocking':result['blocking'],'projection_errors':sum(t['projection']=='blocked' for t in result['tables']),'source_errors':len(result['source_errors'])},ensure_ascii=False),flush=True)
 (BASE/'after_results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
