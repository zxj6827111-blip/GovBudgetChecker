from pathlib import Path
import json,hashlib,subprocess,re
ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker')
OUT=ROOT/'outputs/shiquan_source_coverage_20261109'; CODE=ROOT/'artifacts/shiquan-source-coverage-20261109'
log=(OUT/'pytest-accepted.log').read_text(); assert re.search(r'\d+ passed, 196 skipped',log) and ' failed' not in log
assert json.loads((OUT/'display_verification.json').read_text())['classRows']==11
files=json.loads((OUT/'release_files.json').read_text()); manifest=json.loads((OUT/'implementation_manifest.json').read_text())
assert all(hashlib.sha256((CODE/p).read_bytes()).hexdigest()==v for p,v in manifest.items())
subprocess.run(['git','add','--',*files],cwd=CODE,check=True)
subprocess.run(['git','commit','-m','fix: 核实石泉原格勾稽并完善零值取数与展示'],cwd=CODE,check=True)
print(subprocess.check_output(['git','rev-parse','HEAD'],cwd=CODE,text=True).strip())
