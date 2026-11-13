"""Capture this user's existing daily service runtime without printing credentials."""
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import subprocess
from collections import Counter
from dotenv import dotenv_values

ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker')
OUT=Path(__file__).resolve().parent
libc=ctypes.CDLL('/usr/lib/libSystem.B.dylib',use_errno=True)
def proc_args(pid):
 mib=(ctypes.c_int*3)(1,49,pid)
 size=ctypes.c_size_t()
 if libc.sysctl(mib,3,None,ctypes.byref(size),None,0)!=0:raise OSError(ctypes.get_errno(),'proc args')
 buf=ctypes.create_string_buffer(size.value)
 if libc.sysctl(mib,3,buf,ctypes.byref(size),None,0)!=0:raise OSError(ctypes.get_errno(),'proc args')
 data=buf.raw[:size.value];argc=int.from_bytes(data[:4],byteorder='little');pos=data.index(b'\0',4)+1
 while pos<len(data) and data[pos]==0:pos+=1
 args=[]
 for _ in range(argc):
  end=data.index(b'\0',pos);args.append(data[pos:end].decode(errors='replace'));pos=end+1
 entries=[x.decode(errors='replace') for x in data[pos:].split(b'\0') if b'=' in x]
 return args,dict(x.split('=',1) for x in entries)
def cwd(pid):
 text=subprocess.check_output(['lsof','-a','-p',str(pid),'-d','cwd','-Fn'],text=True)
 return next(x[1:] for x in text.splitlines() if x.startswith('n'))
def private_write(path,data):
 path.write_text(json.dumps(data,ensure_ascii=False,indent=2));path.chmod(0o600)

listeners={}
for port in [18743,3000]:
 raw=subprocess.run(['lsof','-t',f'-iTCP:{port}','-sTCP:LISTEN'],capture_output=True,text=True,check=True)
 listeners[str(port)]=sorted({int(x) for x in raw.stdout.split()})
all_ps=subprocess.check_output(['ps','-axo','pid=,ppid=,pgid=,command='],text=True)
candidates=set(sum(listeners.values(),[]))
for line in all_ps.splitlines():
 parts=line.strip().split(None,3)
 if len(parts)==4 and any(m in parts[3] for m in ['api.worker','dev-backend.cjs','scripts/dev.cjs']):
  candidates.add(int(parts[0]))
processes=[]
for pid in sorted(candidates):
 try:
  wd=cwd(pid)
  if wd not in [str(ROOT/'artifacts/daily-main'),str(ROOT/'artifacts/daily-main/app')]:continue
  argv,env=proc_args(pid)
  parent=subprocess.check_output(['ps','-p',str(pid),'-o','ppid=,pgid='],text=True).split()
  processes.append({'pid':pid,'ppid':int(parent[0]),'pgid':int(parent[1]),'cwd':wd,'argv':argv,'environment':env})
 except (ProcessLookupError,subprocess.CalledProcessError):continue
private_write(OUT/'private/live_processes.json',{'listeners':listeners,'processes':processes})
root_env={k:v for k,v in dotenv_values(ROOT/'.env').items() if v is not None}
api=next(p for p in processes if p['pid'] in listeners['18743'] and 'api.main:app' in p['argv'])
frontend=next(p for p in processes if p['pid'] in listeners['3000'])
effective={}
for label,proc in [('api',api),('frontend',frontend)]:
 env=dict(root_env);env.update(proc['environment']);effective[label]=env
private_write(OUT/'private/effective_environments.json',effective)
paths={
 'uploads':str(Path(effective['api'].get('UPLOAD_DIR') or ROOT/'uploads').resolve()),
 'org_data':str(Path(effective['api'].get('ORG_DATA_DIR') or ROOT/'data').resolve()),
 'user_data':str(Path(effective['api'].get('USER_DATA_DIR') or ROOT/'data').resolve()),
}
counts=Counter();jobs=[]
for f in Path(paths['uploads']).glob('*/status.json'):
 try:
  status=json.loads(f.read_text());state=status.get('status');counts[state]+=1
  jobs.append({'id':f.parent.name,'status':state,'size':f.stat().st_size,'sha256':hashlib.sha256(f.read_bytes()).hexdigest()})
 except (ValueError,OSError):continue
private_write(OUT/'private/historical_jobs_before.json',jobs)
safe={'listeners':listeners,'processes':[{'pid':p['pid'],'ppid':p['ppid'],'pgid':p['pgid'],'cwd':p['cwd'],'program':p['argv'][:7]} for p in processes],
 'queue':{k:effective['api'].get(k) for k in ['JOB_QUEUE_ROLE','JOB_QUEUE_ENABLED','JOB_QUEUE_WORKERS','JOB_QUEUE_RESUME_ON_START','AI_SEQUENTIAL_MODE']},
 'paths':paths,'database_configured':bool(effective['api'].get('DATABASE_URL')),
 'database_identity_sha256':hashlib.sha256(effective['api'].get('DATABASE_URL','').encode()).hexdigest(),
 'root_env_sha256':hashlib.sha256((ROOT/'.env').read_bytes()).hexdigest(),
 'job_status_counts':dict(counts),'historical_jobs':len(jobs)}
(OUT/'runtime_before.json').write_text(json.dumps(safe,ensure_ascii=False,indent=2))
print(json.dumps(safe,ensure_ascii=False))
