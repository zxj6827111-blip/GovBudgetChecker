import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import time

ROOT=Path(__file__).resolve().parents[1]
PRIVATE=ROOT/'runtime_private_2';OUT=ROOT/'live_receipts_2'
APP=Path('/Volumes/workSSD/software development/GovBudgetChecker/artifacts/daily-main/app')
NODE='/Users/zhangxingjin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node'

def main():
    owned=json.loads((PRIVATE/'owned_processes.json').read_text());access=json.loads((PRIVATE/'browser_access.json').read_text())
    frontend=None;browser_status=None;cleanup={'daily_service_touched':False,'owned_api_worker_stopped':False,'owned_database_stopped':False,'owned_frontend_stopped':False}
    try:
        front=PRIVATE/'frontend';front.mkdir()
        shutil.copytree(APP/'.next',front/'.next',symlinks=True)
        if(APP/'public').exists():shutil.copytree(APP/'public',front/'public',symlinks=True)
        shutil.copyfile(APP/'package.json',front/'package.json')
        for name in ['next.config.js','next.config.mjs','next.config.ts']:
            if(APP/name).exists():shutil.copyfile(APP/name,front/name)
        (front/'node_modules').symlink_to(APP/'node_modules',target_is_directory=True)
        with socket.socket()as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
        env={k:v for k,v in os.environ.items()if k in {'PATH','HOME','LANG','LC_ALL','SHELL','TZ'}}
        env.update({'BACKEND_URL':f"http://127.0.0.1:{access['api_port']}",'GOVBUDGET_API_KEY':access['api_key'],
             'BACKEND_API_KEY':access['api_key'],'USER_SESSION_SECRET':access['session_secret'],'NODE_ENV':'production','NEXT_TELEMETRY_DISABLED':'1'})
        with(PRIVATE/'logs/frontend.log').open('wb')as log:
            frontend=subprocess.Popen([NODE,str(APP/'node_modules/next/dist/bin/next'),'start','--hostname','127.0.0.1','-p',str(port)],cwd=front,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        (PRIVATE/'frontend_access.json').write_text(json.dumps({'port':port}))
        import httpx
        for _ in range(30):
            try:
                if httpx.get(f'http://127.0.0.1:{port}/login').status_code==200:break
            except httpx.HTTPError:pass
            time.sleep(1)
        else:raise RuntimeError('isolated frontend startup failed')
        r=subprocess.run([NODE,str(ROOT/'scripts/independent_browser_probe.cjs')],capture_output=True,timeout=120)
        (PRIVATE/'logs/browser.log').write_bytes(r.stdout+r.stderr)
        browser_status='PASS'if r.returncode==0 and(OUT/'independent_browser_report.json').exists()else'FAIL'
    except Exception as e:
        browser_status='FAIL'
        (OUT/'independent_browser_environment_failure.json').write_text(json.dumps({'error_type':type(e).__name__}))
    finally:
        if frontend:
            frontend.terminate()
            try:frontend.wait(timeout=15)
            except subprocess.TimeoutExpired:frontend.kill();frontend.wait()
            cleanup['owned_frontend_stopped']=True
        for pid in owned['pids']:
            try:os.kill(pid,signal.SIGTERM)
            except ProcessLookupError:pass
        time.sleep(2)
        cleanup['owned_api_worker_stopped']=True
        r=subprocess.run(['docker','stop',owned['container']],capture_output=True)
        cleanup['owned_database_stopped']=r.returncode==0
        cleanup['browser_result']=browser_status
        (OUT/'independent_cleanup.json').write_text(json.dumps(cleanup,ensure_ascii=False,indent=2))
        report=json.loads((OUT/'independent_live_report.json').read_text())
        report.update(temporarily_running_for_browser=False,private_database_stopped=cleanup['owned_database_stopped'],
                      browser_reexecuted=True,browser_result=browser_status)
        (OUT/'independent_live_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(cleanup,ensure_ascii=False))

if __name__=='__main__':main()
