"""Deploy the verified dual-evidence followup without changing the frontend or persistent data."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import urllib.request

ROOT = Path('/Volumes/workSSD/software development/GovBudgetChecker')
OUT = Path(__file__).resolve().parent
MAIN = ROOT / 'artifacts/daily-main'
WORK = ROOT / 'artifacts/claude-closeout-20261108'


def git(directory, *args):
    return subprocess.check_output(['git', '-C', str(directory), *args], text=True).strip()


def listeners(port):
    return {int(x) for x in subprocess.run(['lsof', '-t', f'-iTCP:{port}', '-sTCP:LISTEN'],
                                         capture_output=True, text=True).stdout.split()}


def cwd(pid):
    value = subprocess.check_output(['lsof', '-a', '-p', str(pid), '-d', 'cwd', '-Fn'], text=True)
    return next(line[1:] for line in value.splitlines() if line.startswith('n'))


def stop_backend(supervisor):
    assert cwd(supervisor) == str(MAIN)
    os.kill(supervisor, signal.SIGTERM)
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if not listeners(18743):
            return
        time.sleep(.2)
    for pid in listeners(18743):
        assert cwd(pid) == str(MAIN)
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if not listeners(18743):
            return
        time.sleep(.2)
    raise RuntimeError('backend port not released')


def launch():
    env = json.loads((OUT / 'private/effective_environments.json').read_text())['api']
    with (OUT / 'backend-followup.log').open('ab') as log:
        proc = subprocess.Popen(['/opt/homebrew/bin/node', str(MAIN / 'scripts/dev-backend.cjs')],
                                cwd=MAIN, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                start_new_session=True)
    return proc.pid


def ready():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + 100
    while time.monotonic() < deadline:
        try:
            for path in ['/health', '/ready']:
                with opener.open('http://127.0.0.1:18743' + path, timeout=4) as response:
                    assert response.status == 200
            return
        except Exception:
            time.sleep(.5)
    raise RuntimeError('backend readiness timeout')


old, new = git(MAIN, 'rev-parse', 'HEAD'), git(WORK, 'rev-parse', 'HEAD')
assert old == 'a35db4b855839aa3197a4005f5e7514193588f5d'
assert git(MAIN, 'branch', '--show-current') == 'main'
assert not git(MAIN, 'status', '--porcelain', '-uno')
assert set(git(MAIN, 'diff', '--name-only', old, new).splitlines()) == {
    'src/engine/native_issue_dedup.py', 'tests/test_missing_table_dual_evidence.py'}
assert not [path.parent.name for path in (ROOT / 'uploads').glob('*/status.json')
            if json.loads(path.read_text()).get('status') in {'queued', 'running', 'processing', 'analyzing'}]
records = json.loads((OUT / 'processes.json').read_text())
frontend_pids = listeners(3000)
assert frontend_pids
stop_backend(records['backend']['pid'])
new_pid = None
try:
    subprocess.run(['git', 'merge', '--ff-only', new], cwd=MAIN, check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    new_pid = launch()
    ready()
    assert listeners(3000) == frontend_pids
    records['backend']['pid'] = new_pid
    (OUT / 'processes.json').write_text(json.dumps(records, indent=2))
    source = json.loads((OUT / 'source_preservation.json').read_text())
    source.update(candidate=new, previous_candidate=old)
    source['native_issue_dedup_sha256'] = hashlib.sha256((MAIN / 'src/engine/native_issue_dedup.py').read_bytes()).hexdigest()
    (OUT / 'source_preservation.json').write_text(json.dumps(source, indent=2))
    receipt = {'before': old, 'after': new, 'backend_pid': new_pid, 'backend_listener_pids': sorted(listeners(18743)),
               'frontend_unchanged': True, 'health_ready_200': True}
    (OUT / 'backend_followup_receipt.json').write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt), flush=True)
except Exception:
    if new_pid:
        stop_backend(new_pid)
    if git(MAIN, 'rev-parse', 'HEAD') != old:
        subprocess.run(['git', 'switch', '--detach', old], cwd=MAIN, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    launch()
    ready()
    raise
