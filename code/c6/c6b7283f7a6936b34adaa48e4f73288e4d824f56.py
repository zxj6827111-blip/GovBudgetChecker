from pathlib import Path
import base64,hashlib,json,os,subprocess,sys,time,uuid
import httpx
ROOT=Path(__file__).resolve().parents[2]; OUT=Path(__file__).resolve().parent
name='gbc-ocr-review12-'+uuid.uuid4().hex[:10]
image='govbudgetchecker-ocr:round11-offline-current'
info={'scope':'independent development service checks; no frozen business acceptance','container':name,'cold_offline':{},'live':{},'errors':[]};started=time.monotonic()
def call(args,**kw):return subprocess.run(args,check=True,capture_output=True,text=True,timeout=kw.pop('timeout',120),**kw).stdout.strip()
def run(network):
 args=['docker','run','-d','--name',name,'--read-only','--tmpfs','/tmp:rw,size=512m','--tmpfs','/root/.cache:rw,size=512m','-e','OCR_CPU_THREADS=2','-e','PADDLE_HOME=/models']
 if network=='none':args += ['--network','none']
 else:args += ['-p','127.0.0.1::9010']
 return call(args+[image])
def remove():subprocess.run(['docker','rm','-f',name],capture_output=True)
def save_log(label):
 r=subprocess.run(['docker','logs',name],capture_output=True,text=True);(OUT/(label+'.log')).write_text(r.stdout+r.stderr)
try:
 info['image_id']=call(['docker','image','inspect','--format','{{.Id}}',image])
 run('none'); info['cold_offline']['container_configuration']=json.loads(call(['docker','inspect','--format','{{json .HostConfig}}',name]))
 health_script="import httpx,json; print(json.dumps(httpx.get('http://127.0.0.1:9010/health',timeout=2).json()))"
 for i in range(90):
  r=subprocess.run(['docker','exec',name,'python','-c',health_script],capture_output=True,text=True,timeout=10)
  if r.returncode==0:
   health=json.loads(r.stdout)
   if health.get('models_ready'):break
  time.sleep(1)
 else:raise TimeoutError('cold offline OCR not ready')
 expected=hashlib.sha256((ROOT/'models/MANIFEST.sha256').read_bytes()).hexdigest()
 assert health['model_fingerprint']==expected
 info['cold_offline']['health']=health
 code="import json,sys,base64,httpx,hashlib; from pathlib import Path; p=json.load(sys.stdin); d=base64.b64decode(p['png']); r=httpx.post('http://127.0.0.1:9010/ocr',files={'image':('control.png',d,'image/png')},timeout=180); r.raise_for_status(); v=r.json(); print(json.dumps(v,ensure_ascii=False))"
 png=ROOT/'outputs/remediation_20261006_round11/development_materials/synthetic_ocr_control_scan.png'
 payload=json.dumps({'png':base64.b64encode(png.read_bytes()).decode()})
 raw=call(['docker','exec','-i',name,'python','-c',code],input=payload,timeout=210); response=json.loads(raw)
 assert response['input_sha256']==hashlib.sha256(png.read_bytes()).hexdigest() and response['model_fingerprint']==expected
 (OUT/'ocr_cold_response.json').write_text(json.dumps(response,ensure_ascii=False,indent=2)+'\n')
 info['cold_offline']['inference']={'input_sha256':response['input_sha256'],'schema_version':response['schema_version'],'tables':len(response['tables']),'blocks':len(response['blocks']),'raw_prediction_sha256':response['raw_prediction_sha256']}
 meta_code="import json,hashlib,importlib.metadata;from pathlib import Path; print(json.dumps({'packages':{p:importlib.metadata.version(p) for p in ['paddlepaddle','paddleocr','paddlex','fastapi','httpx']},'source_sha':{p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in ['ocr_service/app.py','ocr_service/protocol.py']}}))"
 info['cold_offline']['loaded_code_and_packages']=json.loads(call(['docker','exec',name,'python','-c',meta_code]))
 assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in info['cold_offline']['loaded_code_and_packages']['source_sha'].items())
 info['cold_offline']['pip_check']=call(['docker','exec',name,'python','-m','pip','check'])
 save_log('ocr_cold_start');remove();print('cold offline inference verified',flush=True)
 run('bridge'); mapping=call(['docker','port',name,'9010/tcp']);port=int(mapping.rsplit(':',1)[1]);url=f'http://127.0.0.1:{port}'
 for i in range(90):
  try:
   live_health=httpx.get(url+'/health',timeout=2).json()
   if live_health.get('models_ready'):break
  except (httpx.HTTPError,ValueError):pass
  time.sleep(1)
 else:raise TimeoutError('live OCR not ready')
 assert live_health['model_fingerprint']==expected and live_health['pipeline_fingerprint']==health['pipeline_fingerprint']
 info['live']['health']=live_health;info['live']['url']=url
 base=ROOT/'outputs/remediation_20261006_round11/development_materials'
 cmd=[sys.executable,'scripts/run_isolated_service_validation.py','--output',str(OUT/'live_services'),'--budget-pdf',str(base/'普陀区委社会工作部2025年部门预算.pdf'),'--scanned-budget-pdf',str(base/'derived_scanned_budget.pdf'),'--mixed-final-pdf',str(base/'derived_mixed_final.pdf'),'--ocr-control-pdf',str(base/'synthetic_ocr_control_scan.pdf'),'--ocr-url',url,'--ocr-model-fingerprint',expected,'--timeout-seconds','420']
 env=os.environ.copy();env['PYTHON_DOTENV_DISABLED']='1'
 with (OUT/'live_services.log').open('w') as log:r=subprocess.run(cmd,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=1200)
 info['live']['command']=cmd;info['live']['exit_code']=r.returncode
 save_log('ocr_live'); assert r.returncode==0,'live service run failed'
 info['status']='passed'
except Exception as exc:
 info['errors'].append(type(exc).__name__+': '+str(exc));info['status']='failed';save_log('ocr_failure')
finally:
 remove();info['cleanup']='only this OCR container removed; service script cleans own API/Worker/PG';info['elapsed_seconds']=round(time.monotonic()-started,2)
 (OUT/'ocr_service_review.json').write_text(json.dumps(info,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'status':info['status'],'errors':info['errors'],'elapsed_seconds':info['elapsed_seconds']},ensure_ascii=False),flush=True)
