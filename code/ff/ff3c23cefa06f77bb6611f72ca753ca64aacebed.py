"""只替换本任务拥有的隔离OCR容器，现有业务容器不在操作范围内。"""
from pathlib import Path
import subprocess,json,datetime
ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker')
out=ROOT/'artifacts/pdf-input-v2-20261018-orientation'
old='gbc-pdf-v2-ocr-stitch-20261018';new='gbc-pdf-v2-ocr-orientation-20261018'
def run(args):return subprocess.run(args,check=True,capture_output=True,text=True).stdout
info=json.loads(run(['docker','inspect',old]))[0]
assert info['Config'].get('Labels',{}).get('codex.task')=='pdf-input-v2-20261018-stitch'
assert info['HostConfig']['PortBindings']['9010/tcp']==[{'HostIp':'127.0.0.1','HostPort':'19017'}]
logs=subprocess.run(['docker','logs','--timestamps','--tail','200',old],capture_output=True,text=True)
(out/'previous-task-ocr-container.log').write_text(logs.stdout+logs.stderr)
run(['docker','stop','--time','20',old]);run(['docker','rm',old])
args=['docker','run','-d','--name',new,'--label','codex.task=pdf-input-v2-20261018-orientation',
 '--cpus','4','--memory','6g','-p','127.0.0.1:19017:9010',
 '-v',str(ROOT/'models')+':/models:ro',
 '-v',str(out/'implementation/ocr_service')+':/app/ocr_service:ro',
 '-e','OCR_CPU_THREADS=4','-e','OMP_NUM_THREADS=4','-e','OPENBLAS_NUM_THREADS=4',
 '-e','PADDLE_HOME=/models','-e','PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True',
 'govbudgetchecker-ocr:round11-offline-current']
container=run(args).strip()
(out/'isolated-container-start.json').write_text(json.dumps({'container':new,'id':container,'replaced_task_container':old,'labels_verified':True,'cpu':4,'memory_bytes':6*1024**3,'host_port':19017,'models_read_only':True,'time_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()},indent=2))
print(json.dumps({'started':new,'port':19017,'cpu':4,'memory_gb':6}))
