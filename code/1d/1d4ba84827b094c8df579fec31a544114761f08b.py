"""Preserve main's local artifacts, then bind the existing daily paths explicitly."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker')
MAIN=ROOT/'artifacts/daily-main'
OUT=Path(__file__).resolve().parent
assert subprocess.check_output(['git','-C',str(MAIN),'rev-parse','HEAD'],text=True).strip()=='ed9478655bc38266cbea0d2f5e23928249435e28'
assert not subprocess.check_output(['git','-C',str(MAIN),'status','--porcelain'],text=True).strip()
envs=json.loads((OUT/'private/effective_environments.json').read_text())
safe=json.loads((OUT/'runtime_before.json').read_text())
assert not any(safe['job_status_counts'].get(k,0) for k in ['running','queued','processing','analyzing'])
links=[]
backup=OUT/'private/main_local_before';backup.mkdir(mode=0o700)
def link(destination,source):
 source=Path(source);assert source.exists(),str(source)
 if destination.is_symlink() and destination.resolve()==source.resolve():return
 prior=None
 if destination.exists() or destination.is_symlink():
  prior=backup/str(destination.relative_to(MAIN)).replace('/','__')
  destination.rename(prior)
 destination.symlink_to(source,target_is_directory=source.is_dir())
 links.append({'path':str(destination),'target':str(source),'previous_saved':str(prior) if prior else None})
for name,source in [('.env',ROOT/'.env'),('.venv',ROOT/'.venv'),('data',ROOT/'data'),('uploads',Path(safe['paths']['uploads'])),('logs',ROOT/'logs')]:
 link(MAIN/name,source)
for name in ['.env','.env.local','.env.development','.env.development.local','.env.production','.env.production.local']:
 if (ROOT/'app'/name).exists():link(MAIN/'app'/name,ROOT/'app'/name)

api=envs['api'];front=envs['frontend']
api_paths={'UPLOAD_DIR':safe['paths']['uploads'],'ORG_DATA_DIR':safe['paths']['org_data'],
 'USER_DATA_DIR':safe['paths']['user_data'],'AUDIT_LOG_PATH':ROOT/'data/audit/admin-actions.jsonl',
 'AI_MODEL_SETTINGS_PATH':ROOT/'data/ai-model-settings.json',
 'DISCLOSURE_CALENDAR_PATH':ROOT/'data/disclosure-calendar.json'}
for key,default in api_paths.items():
 old=api.get(key)
 api[key]=str((Path(old).expanduser() if Path(old).expanduser().is_absolute() else ROOT/old).resolve()) if old else str(default)
front.update(LOCAL_DATA_DIR=safe['paths']['org_data'],LOCAL_UPLOADS_DIR=safe['paths']['uploads'],
             BACKEND_URL='http://127.0.0.1:18743',GOVBUDGET_BACKEND_PORT='18743')
for label,env in envs.items():
 env.update(PWD=str(MAIN if label=='api' else MAIN/'app'),INIT_CWD=str(MAIN),
            npm_package_json=str(MAIN/('package.json' if label=='api' else 'app/package.json')),
            DEV_START_LOCAL_SERVICES='false',PYTHONDONTWRITEBYTECODE='1')
 # 主机数据库/OCR保持原实例，启动器不操作这些依赖。
 if env.get('GOVBUDGET_BACKEND_LOG') and not Path(env['GOVBUDGET_BACKEND_LOG']).is_absolute():
  env['GOVBUDGET_BACKEND_LOG']=str(ROOT/env['GOVBUDGET_BACKEND_LOG'])
 # 与测试中已确认的httpx兼容问题保持一致，仅归一现有IPv6 bypass写法。
 for key in ['NO_PROXY','no_proxy']:
  if key in env:env[key]=','.join(x.strip().strip('[]') for x in env[key].split(','))
 env.pop('TESTING',None)
p=OUT/'private/release_environments.json';p.write_text(json.dumps(envs,ensure_ascii=False,indent=2));p.chmod(0o600)
(OUT/'path_bindings.json').write_text(json.dumps(links,ensure_ascii=False,indent=2))
assert hashlib.sha256(api.get('DATABASE_URL','').encode()).hexdigest()==safe['database_identity_sha256']
assert hashlib.sha256((ROOT/'.env').read_bytes()).hexdigest()==safe['root_env_sha256']
print(json.dumps({'links':len(links),'database_identity_preserved':True,'root_config_unchanged':True,'upload_root':api['UPLOAD_DIR']},ensure_ascii=False))
