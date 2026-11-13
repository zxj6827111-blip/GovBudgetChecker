import subprocess,sys
from pathlib import Path
root=Path.cwd();out=root/'outputs/unified_remediation_20261106';python='/Volumes/workSSD/software development/GovBudgetChecker/.venv/bin/python'
for label,code in [('baseline_main','/Volumes/workSSD/software development/GovBudgetChecker/artifacts/daily-main'),('candidate_d1',str(root)),('candidate_d2',str(root))]:
 print('START',label,flush=True)
 cmd=[python,str(root/'scripts/verify_pending_review.py'),'collect','--kit',str(out/'kit'),'--code-root',code,'--out',str(out/label),'--sets','RA,RB','--jobs','1','--timeout','600','--python',python,'--label',label,'--keep-work']
 with (out/(label+'.log')).open('w') as log:
  p=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT)
 print('DONE',label,p.returncode,flush=True)
 if p.returncode:sys.exit(p.returncode)
