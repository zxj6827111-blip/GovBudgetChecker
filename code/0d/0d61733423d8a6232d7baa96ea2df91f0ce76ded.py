from pathlib import Path
import json,os,signal,subprocess
run=Path(__file__).resolve().parents[1];s=json.loads((run/'run_state.json').read_text());stopped=[]
front=json.loads((run/'frontend_state.json').read_text())
for pid in [front['pid'],*s['procs'].values()]:
 try:os.kill(pid,signal.SIGTERM);stopped.append(pid)
 except ProcessLookupError:pass
r=subprocess.run(['docker','stop',s['container']],capture_output=True,text=True)
assert r.returncode==0
(run/'cleanup.json').write_text(json.dumps({'validation_processes_signaled':len(stopped),'postgres_stopped':True,'daily_service_touched':False}))
print('Stopped only this round validation stack')
