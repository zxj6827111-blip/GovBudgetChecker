import json,sys
from pathlib import Path
from audit_current import audit
ROOT=Path(__file__).resolve().parents[2]
BASE=Path(__file__).resolve().parent
manifest=json.loads((ROOT/'outputs/putuo_fresh_batch_20261014/sample_manifest.json').read_text())
results=[]
for item in manifest['selected']:
 try:
  result=audit(Path(item['source_pdf']),BASE/'after'/item['sample_id'])
  result['sample_id']=item['sample_id']
  print(json.dumps({'id':item['sample_id'],'pages':result['pages'],'blockers':json.loads((BASE/'after'/item['sample_id']/'coverage.json').read_text())['blocking_total'],'table_blocks':sum(t['projection']=='blocked' for t in result['tables'])},ensure_ascii=False),flush=True)
 except Exception as e:
  result={'sample_id':item['sample_id'],'error':str(e)};print(result,flush=True)
 results.append(result)
 (BASE/'results_after.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
