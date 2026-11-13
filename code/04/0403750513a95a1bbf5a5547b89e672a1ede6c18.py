"""Read only our own running job's non-secret progress fields."""
from pathlib import Path
import tempfile,json
OUT=Path(__file__).resolve().parent
first=json.loads((OUT/'final_delivery_services/native_budget/status.json').read_text())['job_id']
roots=[p for p in Path(tempfile.gettempdir()).glob('govbudget-isolated-*') if (p/'uploads'/first/'status.json').exists()]
if not roots:
    print(json.dumps({'state':'temporary service workspace no longer present; inspect final receipt'}))
else:
    assert len(roots)==1,len(roots)
    rows=[]
    for p in (roots[0]/'uploads').glob('*/status.json'):
        d=json.loads(p.read_text())
        if d.get('status') not in ['review_required','done','error','failed']:
            rows.append({k:d.get(k) for k in ['job_id','status','progress','pipeline_budget','ocr_progress']})
    print(json.dumps(rows,ensure_ascii=False))
