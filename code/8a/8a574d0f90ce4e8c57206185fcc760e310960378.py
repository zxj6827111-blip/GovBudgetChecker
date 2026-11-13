
import json,sys,hashlib,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from scripts.verify_native_pdf_core import audit
BASE=Path(__file__).resolve().parent
items=json.loads((BASE/'inventory.json').read_text())
source_paths=['src/services/pdf_parser.py','src/services/native_dimension_recovery.py','src/engine/native_final_disclosure.py','src/engine/native_narrative_checks.py','src/engine/rules_v33.py']
source_hashes={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in source_paths}
results=[]
for item in items:
    before=ROOT/'outputs/check_supplement_fix_20261013/after'/item['job_id']
    output=BASE/'after'/item['job_id']
    result=audit(Path(item['source_pdf']),output)
    prior=json.loads((before/'coverage.json').read_text())
    current=json.loads((output/'coverage.json').read_text())
    result.update(job_id=item['job_id'],before_blocking=prior['blocking_total'],after_blocking=current['blocking_total'],
                  before_obligations=prior['blocking_obligation_ids'],after_obligations=current['blocking_obligation_ids'])
    if result['sha256']!=item['sha256']:raise RuntimeError('original source hash changed')
    results.append(result)
    (BASE/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
    print(json.dumps({'filename':item['filename'],'year':item['year'],'before':result['before_blocking'],'after':result['after_blocking'],
         'pages':result['pages'],'errors':len(result['source_errors'])+len(result['native_blank_source_errors'])+len(result['native_dimension_source_errors'])+len(result['disclosure_source_errors'])},ensure_ascii=False),flush=True)
summary={'documents':len(results),'departments':len({r['filename'] for r in results}),'pages':sum(r['pages'] for r in results),
         'before_blocking':sum(r['before_blocking'] for r in results),'after_blocking':sum(r['after_blocking'] for r in results),
         'source_errors':sum(len(r['source_errors']) for r in results),'blank_source_errors':sum(len(r['native_blank_source_errors']) for r in results),
         'dimension_source_errors':sum(len(r['native_dimension_source_errors']) for r in results),'disclosure_source_errors':sum(len(r['disclosure_source_errors']) for r in results),
         'facts':sum(r['facts'] for r in results),'source_hashes':source_hashes,
         'source_code_unchanged_during_run':all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==v for p,v in source_hashes.items())}
(BASE/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
print(json.dumps(summary,ensure_ascii=False),flush=True)
