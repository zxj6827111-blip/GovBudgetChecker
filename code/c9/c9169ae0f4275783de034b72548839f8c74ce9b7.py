"""仅更新本地前端构建；保留旧构建、API、配置和原始任务。"""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import urllib.request
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
manifest = json.loads((OUT/'frontend-source-manifest.json').read_text())
assert all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest() == digest for name,digest in manifest.items())
protected = json.loads((OUT/'protected_hashes.json').read_text())
assert all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest() == digest for name,digest in protected.items())
new = OUT/'build-workspace/app/.next'
assert (new/'BUILD_ID').is_file()
previous_api = subprocess.check_output(['lsof','-tiTCP:18743','-sTCP:LISTEN'],text=True).strip()
pids = subprocess.check_output(['lsof','-tiTCP:3000','-sTCP:LISTEN'],text=True).split()
assert len(set(pids)) == 1
pid = pids[0]
command = subprocess.check_output(['ps','-p',pid,'-o','command='],text=True)
cwd = subprocess.check_output(['lsof','-a','-p',pid,'-d','cwd','-Fn'],text=True)
assert 'next-server' in command and f'n{ROOT}/app\n' in cwd
old_id = (ROOT/'app/.next/BUILD_ID').read_text().strip()
os.kill(int(pid),signal.SIGTERM)
for _ in range(100):
    if not subprocess.run(['lsof','-tiTCP:3000','-sTCP:LISTEN'],capture_output=True).stdout:
        break
    time.sleep(.1)
else:
    raise RuntimeError('Frontend did not release port')
(ROOT/'app/.next').rename(OUT/'previous_app_next')
new.rename(ROOT/'app/.next')
env = os.environ.copy()
env.update({key:value for key,value in dotenv_values(ROOT/'.env').items() if value is not None})
env.update(BACKEND_URL='http://127.0.0.1:18743',GOVBUDGET_BACKEND_PORT='18743')
with (OUT/'frontend.log').open('ab') as log:
    process = subprocess.Popen(['node',str(ROOT/'app/node_modules/next/dist/bin/next'),'start','--hostname','127.0.0.1','--port','3000'],
        cwd=ROOT/'app',env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
for _ in range(100):
    try:
        with urllib.request.urlopen('http://127.0.0.1:3000',timeout=2) as response:
            if response.status == 200:
                break
    except OSError:
        pass
    if process.poll() is not None:
        raise RuntimeError('Frontend stopped; retained old build in previous_app_next')
    time.sleep(.1)
else:
    raise RuntimeError('Frontend did not become available; old build retained')
record = {'previous_frontend_pid':int(pid),'frontend_pid':process.pid,'previous_build_id':old_id,
          'build_id':(ROOT/'app/.next/BUILD_ID').read_text().strip(),
          'api_pid':previous_api,'api_unchanged':previous_api == subprocess.check_output(['lsof','-tiTCP:18743','-sTCP:LISTEN'],text=True).strip(),
          'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()}
assert record['api_unchanged']
(OUT/'processes.json').write_text(json.dumps(record,indent=2))
print(json.dumps(record))
