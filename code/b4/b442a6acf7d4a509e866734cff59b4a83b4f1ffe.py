"""启动已合并的本地3000实例，保留原库、原目录与登录配置。"""
import os,json,signal,subprocess,time,shutil
from pathlib import Path
from dotenv import dotenv_values
ROOT=Path(__file__).resolve().parents[2];OUT=Path(__file__).resolve().parent
assert (ROOT/'app/.next/BUILD_ID').exists()
env=os.environ.copy();env.update({k:v for k,v in dotenv_values(ROOT/'.env').items() if v is not None})
env.update(BACKEND_URL='http://127.0.0.1:18743',GOVBUDGET_BACKEND_PORT='18743',JOB_QUEUE_ROLE='all')
# API进程按all角色承载本地队列，无需再启动独立worker。
for port,marker,cwd in [(3000,'next-server',ROOT/'app'),(18743,'uvicorn api.main:app',ROOT)]:
 ids=subprocess.run(['lsof','-tiTCP:'+str(port),'-sTCP:LISTEN'],capture_output=True,text=True).stdout.split()
 for pid_s in set(ids):
  pid=int(pid_s);cmd=subprocess.check_output(['ps','-p',pid_s,'-o','command='],text=True)
  actual=subprocess.check_output(['lsof','-a','-p',pid_s,'-d','cwd','-Fn'],text=True)
  assert marker in cmd and f'n{cwd}\n' in actual,(port,'process identity mismatch')
  os.kill(pid,signal.SIGTERM)
 for _ in range(100):
  if not subprocess.run(['lsof','-tiTCP:'+str(port),'-sTCP:LISTEN'],capture_output=True).stdout:break
  time.sleep(.1)
 else:raise RuntimeError('Original process did not release port')
specs={'api':([str(ROOT/'.venv/bin/python'),'-m','uvicorn','api.main:app','--host','127.0.0.1','--port','18743'],ROOT),'frontend':(['node',str(ROOT/'app/node_modules/next/dist/bin/next'),'start','--hostname','127.0.0.1','--port','3000'],ROOT/'app')}
records={}
for key,(args,cwd) in specs.items():
 with (OUT/f'{key}.log').open('ab') as f:
  proc=subprocess.Popen(args,cwd=cwd,env=env,stdin=subprocess.DEVNULL,stdout=f,stderr=f,start_new_session=True)
 records[key]={'pid':proc.pid,'cwd':str(cwd)}
(OUT/'processes.json').write_text(json.dumps(records,indent=2))
print(json.dumps(records))
