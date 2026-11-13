"""本次执行的串行依赖队列；不注册自动化，不并发提交真实 OCR。"""
from pathlib import Path
import os,sys,time,json,subprocess,datetime,hashlib
BASE=Path(__file__).resolve().parent;ROOT=BASE.parents[1]
progress=BASE/'serial-ocr-validation-progress.json'
def save(**data):progress.write_text(json.dumps({'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),**data},ensure_ascii=False,indent=2))
receipt=BASE/'income-capture-v13/receipt.json'
save(phase='waiting_for_original_capture',dependency=str(receipt),source_pid=38525)
started=time.monotonic()
while not receipt.exists():
 try:os.kill(38525,0)
 except ProcessLookupError:
  save(phase='dependency_exited_without_receipt',source_pid=38525);raise SystemExit(2)
 if time.monotonic()-started>12*3600:
  save(phase='dependency_wait_timeout');raise SystemExit(3)
 time.sleep(15)
assert json.loads(receipt.read_text())['status']!='pending','capture schedule remains pending; do not mistake it for complete'
results=[]
def execute(name,args):
 save(phase='running_'+name,completed=results)
 with (BASE/(name+'.log')).open('w') as log:
  child=subprocess.run(args,stdout=log,stderr=subprocess.STDOUT)
 results.append({'stage':name,'exit_code':child.returncode})
 save(phase='finished_'+name,completed=results)
execute('income-boundaries-v13',[sys.executable,str(BASE/'run_income_boundaries_v13.py')])
source=ROOT/'artifacts/pdf-input-v2-20261018/official-originals/OFFICIAL_95c2b8afdcbf/source.pdf'
assert hashlib.sha256(source.read_bytes()).hexdigest()=='95c2b8afdcbf6f9407569a60e35bd35d21e2ce56aae5f9d4782e4324a9f6c8a4'
execute('isolated-services-real-ocr-v22',[sys.executable,str(BASE/'validation-runtime-v22/scripts/run_isolated_service_validation.py'),
 '--output',str(BASE/'isolated-services-real-ocr-v22'),'--original-scanned-budget-pdf',str(source),
 '--final-pdf',str(ROOT/'samples/bad/中共上海市普陀区委社会工作部 2024 年度部门决算.pdf'),
 '--ocr-url','http://127.0.0.1:19017','--ocr-model-fingerprint','9e20de91cfdde807ce68673637b8c4cb2614998f4082bf9c90c569eec66e6cea',
 '--timeout-seconds','21600'])
save(phase='queue_finished',completed=results,independent_accuracy_acceptance=False)
