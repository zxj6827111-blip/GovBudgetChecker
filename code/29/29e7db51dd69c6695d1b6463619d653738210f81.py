"""独立本地验收实例管理；仅操作本实例登记的进程。"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
WT = ROOT / 'artifacts/pdf-ai-local-acceptance-20261026'
RT = OUT / 'runtime'
REGISTRY = RT / 'processes.json'
EXPECTED_HEAD = '277f8d25290c78cc5f001ddebc1a24f5b3349fab'

def live(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False

def main():
    action = sys.argv[1] if len(sys.argv) > 1 else 'status'
    records = json.loads(REGISTRY.read_text()) if REGISTRY.exists() else {}
    if action == 'stop':
        verified = []
        for label, rec in records.items():
            if not live(rec['pid']):
                continue
            command = subprocess.check_output(['ps', '-p', str(rec['pid']), '-o', 'command='], text=True)
            cwd_info = subprocess.check_output(['lsof', '-a', '-p', str(rec['pid']), '-d', 'cwd', '-Fn'], text=True)
            marker_ok = rec['marker'] in command or (label == 'frontend' and 'next-server' in command)
            if not marker_ok or f'n{rec["cwd"]}\n' not in cwd_info:
                raise RuntimeError(f'{label}: PID identity changed; refusing to stop')
            verified.append(rec['pid'])
        for pid in verified:
            os.kill(pid, signal.SIGTERM)
        for _ in range(50):
            if not any(live(pid) for pid in verified):
                break
            time.sleep(0.2)
        print('Only isolated application processes stopped; database and original services preserved')
        return
    if action == 'status':
        print(json.dumps({label: {'pid': rec['pid'], 'running': live(rec['pid'])} for label, rec in records.items()}))
        return
    assert action == 'start'
    if any(live(rec['pid']) for rec in records.values()):
        raise RuntimeError('Some isolated processes are already running; inspect status first')
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=WT, text=True).strip()
    assert head == EXPECTED_HEAD, 'Frozen checkout changed'
    assert subprocess.check_output(['git', 'diff', 'HEAD', '--name-only'], cwd=WT, text=True).strip() == ''
    subprocess.run(['docker', 'start', 'gbc-pdf-ai-acceptance-20261026'], check=True, stdout=subprocess.DEVNULL)
    env = os.environ.copy()
    env.update(json.loads((RT / 'environment.json').read_text()))
    specs = {
        'api': ([str(WT / '.venv/bin/python'), '-m', 'uvicorn', 'api.main:app', '--host', '127.0.0.1', '--port', '18746'], WT, 'api', '18746'),
        'worker': ([str(WT / '.venv/bin/python'), '-m', 'api.worker'], WT, 'worker', 'api.worker'),
        'frontend': (['node', str(WT / 'app/node_modules/next/dist/bin/next'), 'start', '--hostname', '127.0.0.1', '--port', '3013'], WT / 'app', 'api', 'next-server'),
    }
    for label, (args, cwd, role, marker) in specs.items():
        child_env = dict(env, JOB_QUEUE_ROLE=role)
        with (RT / f'{label}.log').open('ab') as log:
            proc = subprocess.Popen(args, cwd=cwd, env=child_env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        records[label] = {'pid': proc.pid, 'marker': marker, 'cwd': str(cwd)}
        REGISTRY.write_text(json.dumps(records, indent=2))
    for url in ('http://127.0.0.1:18746/health', 'http://127.0.0.1:3013/login'):
        for _ in range(90):
            try:
                with urllib.request.urlopen(url, timeout=2) as response:
                    assert response.status == 200
                break
            except Exception:
                if any(not live(rec['pid']) for rec in records.values()):
                    raise RuntimeError('An isolated process exited; inspect private logs')
                time.sleep(0.5)
        else:
            raise RuntimeError(f'Instance health timeout: {url}')
    print(json.dumps({'frontend': 'http://127.0.0.1:3013', 'backend': 'http://127.0.0.1:18746', 'frozen_head': head, 'processes': records}, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
