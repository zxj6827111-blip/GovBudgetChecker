from pathlib import Path
import hashlib,json,subprocess,sys,time,uuid
import httpx
ROOT=Path(__file__).resolve().parents[2];OUT=Path(__file__).resolve().parent
name='gbc-ocr-round17-'+uuid.uuid4().hex[:10]
image='govbudgetchecker-ocr:round11-offline-current'
subprocess.run(['docker','run','-d','--name',name,'--read-only','--tmpfs','/tmp:rw,size=512m','--tmpfs','/root/.cache:rw,size=512m','-e','OCR_CPU_THREADS=2','-e','PADDLE_HOME=/models','-p','127.0.0.1::9010',image],capture_output=True,text=True,check=True,timeout=60)
info=json.loads(subprocess.check_output(['docker','inspect',name],text=True))[0]
url='http://127.0.0.1:'+info['NetworkSettings']['Ports']['9010/tcp'][0]['HostPort']
runtime={'container':name,'image':image,'image_id':info['Image'],'url':url,'scope':'round17 development technical verification; not independent blind acceptance'}
(OUT/'ocr_runtime.json').write_text(json.dumps(runtime,ensure_ascii=False,indent=2)+'\n')
for _ in range(60):
 try:
  health=httpx.get(url+'/health',timeout=2).json()
  if health.get('models_ready'):break
 except (httpx.HTTPError,ValueError):pass
 time.sleep(1)
else:raise RuntimeError('OCR did not become ready')
runtime['health']=health;runtime['model_fingerprint']=health['model_fingerprint']
files={}
for rel in ('ocr_service/app.py','ocr_service/protocol.py'):
 result=subprocess.check_output(['docker','exec',name,'python','-c',f"import hashlib;print(hashlib.sha256(open('/app/{rel}','rb').read()).hexdigest())"],text=True).strip()
 assert result==hashlib.sha256((ROOT/rel).read_bytes()).hexdigest(),rel
 files[rel]=result
runtime['loaded_source_sha']=files
(OUT/'ocr_runtime.json').write_text(json.dumps(runtime,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(runtime,ensure_ascii=False),flush=True)
