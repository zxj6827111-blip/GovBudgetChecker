"""日常入口启动/停止；只在内存读取原配置，不复制原凭据或业务数据。"""
from pathlib import Path
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from dotenv import dotenv_values

ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker')
WT=ROOT/'artifacts/daily-main'
OUT=ROOT/'outputs/daily_integration_20261105'
LOGS=OUT/'daily-runtime'
VERSION='47581360966191d180880f7fb07dab48974948bb'

def configuration():
    env=dict(os.environ)
    env.update({k:v for k,v in dotenv_values(ROOT/'.env').items() if v is not None})
    defaults={
        'UPLOAD_DIR':ROOT/'uploads', 'ORG_DATA_DIR':ROOT/'data', 'USER_DATA_DIR':ROOT/'data',
        'AI_MODEL_SETTINGS_PATH':ROOT/'data/ai-model-settings.json',
        'DISCLOSURE_CALENDAR_PATH':ROOT/'data/disclosure-calendar.json',
        'GBC_ADMIN_CONFIG_PATH':ROOT/'data/admin_config.json',
        'LOCAL_DATA_DIR':ROOT/'data', 'LOCAL_UPLOADS_DIR':ROOT/'uploads',
        'AUDIT_LOG_PATH':ROOT/'data/local-audit/admin-actions.jsonl',
    }
    for key,default in defaults.items():
        value=Path(env.get(key) or default)
        env[key]=str(value if value.is_absolute() else ROOT/value)
    if env.get('USER_FILE'):
        value=Path(env['USER_FILE']);env['USER_FILE']=str(value if value.is_absolute() else ROOT/value)
    env.update(GOVBUDGET_BACKEND_PORT='18743',BACKEND_URL='http://127.0.0.1:18743',
               GBC_ENABLE_E2E_PAGES='false',PYTHONPATH=str(WT))
    return env

def port_open(port):
    with socket.socket() as sock:
        sock.settimeout(1)
        return sock.connect_ex(('127.0.0.1',port))==0

def start():
    actual=subprocess.check_output(['git','rev-parse','HEAD'],cwd=WT).decode().strip()
    assert actual==VERSION, '日常目录代码与已验收版本不一致'
    assert not port_open(18743) and not port_open(3000), '目标端口已被占用，请先核实进程'
    env=configuration();LOGS.mkdir(exist_ok=True)
    commands={
        'backend':([str(ROOT/'.venv/bin/python'),'-m','uvicorn','api.main:app','--host','127.0.0.1','--port','18743'],WT),
        'frontend':([shutil.which('node'),str(WT/'app/node_modules/next/dist/bin/next'),'start','--hostname','127.0.0.1','--port','3000'],WT/'app'),
    }
    records={}
    for kind,(argv,cwd) in commands.items():
        with (LOGS/f'{kind}.log').open('a') as log:
            child=subprocess.Popen(argv,cwd=cwd,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        records[kind]={'pid':child.pid,'cwd':str(cwd),'log':str(LOGS/f'{kind}.log')}
    (LOGS/'processes.json').write_text(json.dumps(records,indent=2))
    print(json.dumps({'version':VERSION,'entry':'http://localhost:3000','processes':records},ensure_ascii=False,indent=2))

def stop():
    records=json.loads((LOGS/'processes.json').read_text())
    for kind,record in records.items():
        pid=record['pid']
        # 只停止 PID 文件与工作目录同时匹配的本次日常服务，防止 PID 复用误杀。
        probe=subprocess.run(['lsof','-a','-p',str(pid),'-d','cwd','-Fn'],text=True,capture_output=True)
        if 'n'+record['cwd'] in probe.stdout.splitlines():
            os.killpg(pid,signal.SIGTERM)
            print('stopped',kind,pid)
        else:
            print('not running or identity changed',kind)
    deadline=time.monotonic()+10
    while time.monotonic()<deadline and (port_open(18743) or port_open(3000)):
        time.sleep(0.2)

if __name__=='__main__':
    if sys.argv[1:] == ['start']:start()
    elif sys.argv[1:] == ['stop']:stop()
    else:raise SystemExit('usage: daily_runtime.py start|stop')
