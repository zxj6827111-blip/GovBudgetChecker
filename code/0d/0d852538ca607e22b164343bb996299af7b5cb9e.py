from pathlib import Path
import hashlib,json,subprocess,shutil
w=Path('/Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106')
src=Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs/independent_ir3_9631ea55_20261106')
out=w/'outputs/ir3_gap01_fix_20261106'
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=w,text=True).strip()=='9631ea55decc8e5b94531e483a5d907842be292a'
assert not subprocess.check_output(['git','status','--porcelain'],cwd=w,text=True)
assert not out.exists()
out.mkdir();(out/'scripts').mkdir();(out/'receipts').mkdir()
def hashes(root):
 return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
(out/'protected_input_hashes.json').write_text(json.dumps({'independent_root':str(src),'independent':hashes(src),'prior_delivery_root':str(w/'outputs/ir3_residual_fix_20261106'),'prior_delivery':hashes(w/'outputs/ir3_residual_fix_20261106')},ensure_ascii=False,indent=2))
for n in ['independent_boundary_controls.py','extra_controls.py','neighbor_controls.py']:
 shutil.copyfile(src/'scripts'/n,out/'scripts'/n)
shutil.copytree('/private/tmp/gbc-gap01-preflight',out/'preflight')
for source,target in [('effect_fact_sources.py','scripts/effect_fact_sources.py'),('test_effect_fact_source_geometry.py','tests/test_effect_fact_source_geometry.py')]:
 shutil.copyfile(Path('/private/tmp/gbc-gap01-preflight')/source,w/target)
print(out)
