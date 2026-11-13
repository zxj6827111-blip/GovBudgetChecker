"""比较优化前后的工作流内容与读取次数；不提交任何复核写动作。"""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT/'.env')
from api import runtime
from src.services import issue_workflow_store as workflow

assert not any(runtime.get_ignored_issues_path(p).is_file() for p in runtime.iter_job_dirs())
old_path=Path(__file__).parent/'before/src/services/issue_workflow_store.py'
spec=importlib.util.spec_from_file_location('before_detail_workflow',old_path)
old=importlib.util.module_from_spec(spec)
spec.loader.exec_module(old)
records=[]
original=runtime.get_job_status_payload
reads=0
def counted(*args,**kwargs):
    global reads
    reads+=1
    return original(*args,**kwargs)
runtime.get_job_status_payload=counted
admin={'username':'performance-probe','is_admin':True}
def measure(label,fn):
    global reads
    reads=0
    start=time.perf_counter()
    data=fn()
    encoded=json.dumps(data,ensure_ascii=False,sort_keys=True).encode()
    record={'phase':label,'seconds':time.perf_counter()-start,'full_detail_reads':reads,
            'returned_issues':len(data['issues']),'response_sha256':hashlib.sha256(encoded).hexdigest()}
    records.append(record)
    print(json.dumps(record),flush=True)
    return data
try:
    baseline=measure('before_global_workflow',lambda:old.get_visible_state(admin))
    current=measure('after_global_workflow',lambda:workflow.get_visible_state(admin))
    assert current==baseline
    current_jobs=sorted({record['job_id'] for record in baseline['issues'].values()})
    for job_id in ['7be587eeff3114c0232c5f6c56033816',*current_jobs]:
        data=measure('after_scoped_workflow',lambda:workflow.get_visible_state(admin,job_id=job_id))
        expected={**baseline,'issues':{k:v for k,v in baseline['issues'].items() if v['job_id']==job_id},
                  'packages':[p for p in baseline['packages'] if job_id in p['job_ids']]}
        assert data==expected
finally:
    runtime.get_job_status_payload=original
(Path(__file__).parent/'comparison.json').write_text(json.dumps({'mode':'read_only_workflow_functions','identical_global_and_scoped_content':True,'records':records},indent=2))
