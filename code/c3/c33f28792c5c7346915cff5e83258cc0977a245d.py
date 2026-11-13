from pathlib import Path
import hashlib,json,os,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent
info=json.loads((OUT/'ocr_runtime.json').read_text())
base=ROOT/'outputs/remediation_20261006_round11/development_materials'
original=ROOT/'outputs/remediation_20261006_round13/original_materials'
cmd=[sys.executable,'scripts/run_isolated_service_validation.py','--output',str(OUT/'live_services'),
'--budget-pdf',str(base/'普陀区委社会工作部2025年部门预算.pdf'),
'--scanned-budget-pdf',str(base/'derived_scanned_budget.pdf'),
'--mixed-final-pdf',str(base/'derived_mixed_final.pdf'),
'--ocr-control-pdf',str(base/'synthetic_ocr_control_scan.pdf'),
'--original-scanned-budget-pdf',str(original/'zhejiang_budget.pdf'),
'--original-mixed-final-pdf',str(original/'zhejiang_final.pdf'),
'--ocr-url',info['url'],'--ocr-model-fingerprint',info['model_fingerprint'],'--timeout-seconds','3600']
env=os.environ.copy();env['PYTHON_DOTENV_DISABLED']='1'
started=time.monotonic()
with (OUT/'live_services.log').open('w') as log:
 r=subprocess.run(cmd,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=6000)
result={'command':cmd,'exit_code':r.returncode,'elapsed_seconds':round(time.monotonic()-started,2),'scope':'development technical verification without independent frozen truth'}
(OUT/'service_launch_receipt.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(result,ensure_ascii=False),flush=True)
sys.exit(r.returncode)
