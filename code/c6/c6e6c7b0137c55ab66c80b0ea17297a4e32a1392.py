import json,time,os,signal,tempfile,shutil
from pathlib import Path
out=Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs/pdf_acceptance_shanghai2022_20261021')
while True:
 p=out/'runtime/live_receipt.json'
 if p.exists() and json.loads(p.read_text()).get('scenarios'):
  for work in Path(tempfile.gettempdir()).glob('govbudget-isolated-*'):
   if (work/'uploads/6f6b3b9c0af069e1cbf9e407f5d06317').exists():
    shutil.copytree(work/'uploads',out/'ocr_enabled_first_observation/uploads',dirs_exist_ok=True)
  (out/'runtime/resource_stop_reason.json').write_text(json.dumps({'reason':'Default OCR per-page 600s budget expended on sparse cover; preserve terminal first result and continue native-layer full batch in separate profile; scan is tested separately.','not_accuracy_acceptance':True}))
  os.kill(45596,signal.SIGINT);print('first result preserved; isolated runner interrupted',flush=True);break
 time.sleep(1)
