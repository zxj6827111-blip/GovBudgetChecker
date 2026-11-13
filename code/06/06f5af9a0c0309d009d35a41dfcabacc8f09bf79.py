from pathlib import Path
import hashlib,json,subprocess,shutil
w=Path('/Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106');o=w/'outputs/ir3_gap02_fix_20261106';s=Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs/independent_ir3_d71c9ad7_20261106')
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=w,text=True).strip()=='d71c9ad7eb671ff956b2fcf21d814a5e9c0cc640'
assert not subprocess.check_output(['git','status','--porcelain'],cwd=w,text=True)
assert not o.exists()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
seal=json.loads((s/'independent_manifest.json').read_text());assert all(sha(s/e['path'])==e['sha256'] for e in seal['files'])
for line in (s/'SHA256SUMS').read_text().splitlines():
 h,p=line.split('  ',1);assert sha(s/p)==h
roots={'independent618':s.parent/'independent_ir3_9631ea55_20261106','prior1745':w/'outputs/ir3_residual_fix_20261106','gap01_delivery':w/'outputs/ir3_gap01_fix_20261106','current_independent':s}
o.mkdir();(o/'scripts').mkdir();(o/'receipts').mkdir()
protected={k:{'root':str(root),'files':{str(p.relative_to(root)):sha(p) for p in root.rglob('*') if p.is_file()}} for k,root in roots.items()}
assert len(protected['independent618']['files'])==618 and len(protected['prior1745']['files'])==1745
(o/'protected_input_hashes.json').write_text(json.dumps(protected,ensure_ascii=False,indent=2)+'\n')
for n in ['independent_boundary_controls.py','extra_controls.py','neighbor_controls.py','masking_boundary_controls.py','legal_row_label_control.py','exact_masking_pair.py','run_extra.py']:
 shutil.copyfile(s/'scripts'/n,o/'scripts'/n)
shutil.copytree('/private/tmp/gbc-gap02-20261106',o/'preflight')
for n,d in [('effect_fact_sources.py','scripts'),('test_effect_row_label_boundary.py','tests')]:shutil.copyfile(Path('/private/tmp/gbc-gap02-20261106')/n,w/d/n)
print(o)
