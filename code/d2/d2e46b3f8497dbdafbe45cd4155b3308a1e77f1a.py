import json
from pathlib import Path
import subprocess

ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker')
OUT=Path(__file__).resolve().parent
MAIN=ROOT/'artifacts/daily-main'
env=json.loads((OUT/'private/release_environments.json').read_text())['frontend']
env.update(NODE_ENV='production',npm_lifecycle_event='build')
with (OUT/'frontend_build.log').open('w') as log:
 result=subprocess.run(['/opt/homebrew/bin/node',str(MAIN/'scripts/next-build.cjs')],cwd=MAIN,env=env,stdout=log,stderr=subprocess.STDOUT)
(OUT/'build_result.json').write_text(json.dumps({'exit_code':result.returncode}))
print('frontend_build_exit',result.returncode)
raise SystemExit(result.returncode)
