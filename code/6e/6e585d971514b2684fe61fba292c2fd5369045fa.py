from pathlib import Path
import json,subprocess
ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker')
OUT=ROOT/'outputs/claude_closeout_20261108'
WORK=ROOT/'artifacts/claude-closeout-20261108'
env=json.loads((ROOT/'outputs/daily_main_upgrade_20261108/private/release_environments.json').read_text())['frontend']
env.update(NODE_ENV='production',npm_lifecycle_event='build',GBC_ENABLE_E2E_PAGES='false')
with (OUT/'frontend-build.log').open('w') as log:
 result=subprocess.run(['/opt/homebrew/bin/node',str(WORK/'scripts/next-build.cjs')],cwd=WORK,env=env,stdout=log,stderr=subprocess.STDOUT)
(OUT/'build_result.json').write_text(json.dumps({'exit_code':result.returncode}))
print('candidate frontend build exit',result.returncode)
raise SystemExit(result.returncode)
