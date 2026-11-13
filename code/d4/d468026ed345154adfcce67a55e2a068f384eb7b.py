from pathlib import Path
import json, urllib.request, urllib.error, time, hashlib
ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker');OUT=ROOT/'outputs/shiquan_reconciliation_merge_20261109';JOB='23734f26696d2271a1e7246d80329a51'
env=json.loads((ROOT/'outputs/claude_closeout_20261108/private/effective_environments.json').read_text())['api']
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));token=None
before=json.loads((OUT/'preservation_before.json').read_text())
status_path=ROOT/'uploads'/JOB/'status.json'
assert hashlib.sha256(status_path.read_bytes()).hexdigest()==before['statuses'][JOB]
initial=json.loads(status_path.read_text());assert initial['status']=='review_required' and initial['use_ai_assist'] is True

def request(path, body=None):
    headers={'Content-Type':'application/json'}
    if token:headers['X-Session-Token']=token
    q=urllib.request.Request('http://127.0.0.1:18743'+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
    with opener.open(q,timeout=40) as r:return json.loads(r.read())

try:
    login=request('/api/auth/login',{'username':'admin','password':env['DEFAULT_ADMIN_PASSWORD']})
except urllib.error.HTTPError as e:
    raise SystemExit('Normal configured login unavailable: HTTP '+str(e.code)+'; no retry, no credential changes')
token=login['token'];assert login['user'].get('is_admin')
try:
    response=request('/api/jobs/'+JOB+'/reanalyze',{'use_local_rules':True,'use_ai_assist':True,'mode':'dual'})
    (OUT/'reanalyze_api_receipt.json').write_text(json.dumps({'job':JOB,'before_revision':initial['analysis_revision'],'accepted':response,'use_ai_assist':True},ensure_ascii=False,indent=2))
    print('Main document queued through authenticated normal API; prior AI=true preserved',flush=True)
    deadline=time.monotonic()+600;seen=None
    while time.monotonic()<deadline:
        status=json.loads(status_path.read_text())
        state=(status.get('status'),status.get('stage'))
        if state!=seen:print('State',state,flush=True);seen=state
        if status.get('analysis_revision')!=initial['analysis_revision'] and state[0] in {'review_required','done','completed','error','failed'}:
            print('Final',state[0],status.get('analysis_revision'),flush=True)
            assert state[0]=='review_required',state
            break
        time.sleep(3)
    else:raise RuntimeError('Analysis still running; do not resubmit')
finally:
    try:request('/api/auth/logout',{})
    except Exception:pass
