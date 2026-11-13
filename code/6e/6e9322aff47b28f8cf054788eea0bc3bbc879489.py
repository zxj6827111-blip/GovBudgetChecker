"""Checked local release: preserve persistent data and old build; rollback on readiness failure."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
import urllib.request

ROOT = Path('/Volumes/workSSD/software development/GovBudgetChecker')
MAIN = ROOT / 'artifacts/daily-main'
WORK = ROOT / 'artifacts/claude-closeout-20261108'
OUT = Path(__file__).resolve().parent
PRIVATE = OUT / 'private'
SOURCE = json.loads((OUT / 'source_preservation.json').read_text())
NEW, OLD = SOURCE['candidate'], SOURCE['main_before']
NODE = '/opt/homebrew/bin/node'


def git(*args):
    return subprocess.check_output(['git', '-C', str(MAIN), *args], text=True).strip()


def listeners(port):
    return {int(x) for x in subprocess.run(['lsof', '-t', f'-iTCP:{port}', '-sTCP:LISTEN'],
                                         capture_output=True, text=True).stdout.split()}


def cwd(pid):
    output = subprocess.check_output(['lsof', '-a', '-p', str(pid), '-d', 'cwd', '-Fn'], text=True)
    return next(line[1:] for line in output.splitlines() if line.startswith('n'))


def stop(pids):
    for pid in pids:
        try:
            assert cwd(pid) in {str(MAIN), str(MAIN / 'app')}
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, subprocess.CalledProcessError):
            pass
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if not listeners(3000) and not listeners(18743):
            return
        time.sleep(.2)
    for pid in listeners(3000) | listeners(18743):
        assert cwd(pid) in {str(MAIN), str(MAIN / 'app')}
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if not listeners(3000) and not listeners(18743):
            return
        time.sleep(.2)
    raise RuntimeError('daily ports not released')


def launch(args, directory, env, name):
    with (OUT / f'{name}.log').open('ab') as log:
        proc = subprocess.Popen(args, cwd=directory, env=env, stdin=subprocess.DEVNULL,
                                stdout=log, stderr=log, start_new_session=True)
    return {'pid': proc.pid, 'cwd': str(directory)}


def ready():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + 100
    while time.monotonic() < deadline:
        try:
            for port, path in [(18743, '/health'), (18743, '/ready'), (3000, '/login')]:
                with opener.open(f'http://127.0.0.1:{port}{path}', timeout=4) as response:
                    assert response.status == 200
            return
        except Exception:
            time.sleep(.5)
    raise RuntimeError('readiness timeout; see local release logs')


assert git('rev-parse', 'HEAD') == OLD
assert git('branch', '--show-current') == 'main'
assert not git('status', '--porcelain', '--untracked-files=no')
assert subprocess.call(['git', '-C', str(MAIN), 'merge-base', '--is-ancestor', OLD, NEW]) == 0
assert json.loads((OUT / 'build_result.json').read_text())['exit_code'] == 0
assert json.loads((OUT / 'backup_receipt.json').read_text())['database_dump_bytes'] > 0
assert hashlib.sha256((ROOT / '.env').read_bytes()).hexdigest() == json.loads(
    (OUT / 'runtime_before.json').read_text())['root_env_sha256']
active = []
for path in (ROOT / 'uploads').glob('*/status.json'):
    if json.loads(path.read_text()).get('status') in {'queued', 'running', 'processing', 'analyzing'}:
        active.append(path.parent.name)
assert not active, f'active jobs must finish first: {active}'
saved = json.loads((PRIVATE / 'live_processes.json').read_text())
envs = json.loads((ROOT / 'outputs/daily_main_upgrade_20261108/private/release_environments.json').read_text())
front_env = dict(envs['frontend'], NODE_ENV='production', GBC_ENABLE_E2E_PAGES='false')
api_env = json.loads((PRIVATE / 'effective_environments.json').read_text())['api']
old_build = PRIVATE / 'frontend_previous'
assert not old_build.exists()
records = {}
top = [p['pid'] for p in saved['processes'] if p['pid'] in saved['listeners']['3000']
       or any(str(arg).endswith('/scripts/dev-backend.cjs') for arg in p['argv'])]
stop(top)
print('daily service stopped after idle check', flush=True)
try:
    subprocess.run(['git', 'merge', '--ff-only', NEW], cwd=MAIN, check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    shutil.move(str(MAIN / 'app/.next'), old_build)
    shutil.copytree(WORK / 'app/.next', MAIN / 'app/.next', ignore=shutil.ignore_patterns('cache'))
    records['backend'] = launch([NODE, str(MAIN / 'scripts/dev-backend.cjs')], MAIN, api_env, 'backend')
    records['frontend'] = launch([NODE, str(MAIN / 'app/node_modules/next/dist/bin/next'), 'start',
                                  '--hostname', '::', '--port', '3000'], MAIN / 'app', front_env, 'frontend')
    (OUT / 'processes.json').write_text(json.dumps(records, indent=2))
    ready()
    result = {'before': OLD, 'after': NEW, 'processes': records,
              'backend_pids': sorted(listeners(18743)), 'frontend_pids': sorted(listeners(3000)),
              'health_ready_login_200': True, 'persistent_paths_preserved': True}
    (OUT / 'cutover_receipt.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)
except Exception:
    stop([record['pid'] for record in records.values()])
    if old_build.exists():
        if (MAIN / 'app/.next').exists():
            shutil.move(str(MAIN / 'app/.next'), PRIVATE / 'failed_frontend_build')
        shutil.move(str(old_build), MAIN / 'app/.next')
    if git('rev-parse', 'HEAD') != OLD:
        subprocess.run(['git', 'switch', '--detach', OLD], cwd=MAIN, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    launch([NODE, str(MAIN / 'scripts/dev-backend.cjs')], MAIN, api_env, 'rollback_backend')
    launch([NODE, str(MAIN / 'app/node_modules/next/dist/bin/next'), 'start', '--hostname', '::',
            '--port', '3000'], MAIN / 'app', front_env, 'rollback_frontend')
    ready()
    print('restored previous service and build; main ref preserved for diagnosis', flush=True)
    raise
