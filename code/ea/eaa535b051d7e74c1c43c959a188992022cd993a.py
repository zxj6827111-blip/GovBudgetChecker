
import json,sys,hashlib,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from scripts.verify_native_pdf_core import audit
BASE=Path(__file__).resolve().parent
manifest=json.loads((BASE/'sample_manifest.json').read_text())
assert hashlib.sha256((BASE/'frozen_key_field_truth.json').read_bytes()).hexdigest()==(BASE/'truth_sha256.txt').read_text().strip()
fingerprints={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for folder in ['src','api'] for p in (ROOT/folder).rglob('*.py')}
results=[]
for item in manifest['selected']:
    try:
        result=audit(Path(item['source_pdf']),BASE/'baseline'/item['sample_id'])
        cov=json.loads((BASE/'baseline'/item['sample_id']/'coverage.json').read_text())
        result.update(sample_id=item['sample_id'],category=item['category'],blocking=cov['blocking_total'],blocking_ids=cov['blocking_obligation_ids'])
        assert result['sha256']==item['sha256'] and result['source_unchanged']
        results.append(result)
        print(json.dumps({'id':item['sample_id'],'file':item['filename'],'pages':result['pages'],'blocking':result['blocking'],'blocked_tables':sum(t['projection']=='blocked' for t in result['tables']),'source_errors':len(result['source_errors']),'unresolved_rules':len(result['rule_execution']['unresolved_rules'])},ensure_ascii=False),flush=True)
    except Exception as error:
        results.append({'sample_id':item['sample_id'],'filename':item['filename'],'source_pdf':item['source_pdf'],'error':type(error).__name__+': '+str(error)})
        print('FAILED '+item['sample_id']+' '+type(error).__name__,flush=True)
    (BASE/'baseline_results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
(BASE/'baseline_code_fingerprints.json').write_text(json.dumps({'before':fingerprints,'unchanged':all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==v for p,v in fingerprints.items())},ensure_ascii=False,indent=2))
