from pathlib import Path
import json,socket,subprocess,os
run=Path(__file__).resolve().parents[1]
s=json.loads((run/'run_state.json').read_text())
with socket.socket() as sock:
 sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
env={**os.environ,'BACKEND_URL':f"http://127.0.0.1:{s['api_port']}",'GOVBUDGET_API_KEY':s['api_key'],'BACKEND_API_KEY':s['api_key'],'USER_SESSION_SECRET':s['session_secret'],'NODE_ENV':'production'}
app=Path('/Volumes/workSSD/software development/GovBudgetChecker/artifacts/daily-main/app')
with (run/'frontend.log').open('wb') as out:
 p=subprocess.Popen(['node',str(app/'node_modules/next/dist/bin/next'),'start','--hostname','127.0.0.1','-p',str(port)],cwd=app,env=env,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
(run/'frontend_state.json').write_text(json.dumps({'pid':p.pid,'port':port,'scope':'isolated backend; unchanged compiled Shanghai frontend'}))
print('Started isolated frontend on',port)
