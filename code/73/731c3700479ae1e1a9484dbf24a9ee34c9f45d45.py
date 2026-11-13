import subprocess,shlex,os,signal,json
from pathlib import Path
cwd="/Volumes/workSSD/software development/GovBudgetChecker/artifacts/pdf-input-v2-20261018-stitch/implementation"
found=[]
for line in subprocess.check_output(["ps","-axo","pid=,command="],text=True).splitlines():
 parts=line.strip().split(None,1)
 if len(parts)!=2:continue
 try:argv=shlex.split(parts[1])
 except ValueError:continue
 if len(argv)==2 and "python" in Path(argv[0]).name and argv[1]=="../capture_development_page.py":
  pid=int(parts[0]);where=subprocess.check_output(["lsof","-a","-p",str(pid),"-d","cwd","-Fn"],text=True)
  assert "\nn"+cwd+"\n" in "\n"+where
  os.kill(pid,signal.SIGTERM);found.append(pid)
print({"interrupted_task_capture_pids":found})
out=Path(cwd).parent/"captured-development-page"
(out/"invalid-capture-status.json").write_text(json.dumps({"status":"invalid_harness_model_pin","reason":"direct capture omitted OCR_MODEL_FINGERPRINT","usable_for_acceptance":False,"interrupted_task_pids":found},indent=2))
