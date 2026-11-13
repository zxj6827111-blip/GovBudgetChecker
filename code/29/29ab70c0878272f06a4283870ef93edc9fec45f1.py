from pathlib import Path
import hashlib,json,shutil
root=Path('/Volumes/workSSD/software development/GovBudgetChecker')
out=root/'outputs/pdf_review_remediation_merge_20261027T154139Z'
workspace=out/'build-workspace'
assert not workspace.exists(), workspace
workspace.mkdir()
ignored=shutil.ignore_patterns('node_modules','.next','.env','.env.*','*.tsbuildinfo','.DS_Store')
shutil.copytree(root/'app',workspace/'app',ignore=ignored,symlinks=True)
(workspace/'app/node_modules').symlink_to(root/'app/node_modules',target_is_directory=True)
(workspace/'scripts').mkdir()
shutil.copy2(root/'scripts/next-build.cjs',workspace/'scripts/next-build.cjs')
hashes={}
for p in sorted((workspace/'app').rglob('*')):
 if p.is_file() and not p.is_symlink() and 'node_modules' not in p.parts:
  relative=p.relative_to(workspace/'app')
  original=root/'app'/relative
  assert original.is_file(),relative
  copied=hashlib.sha256(p.read_bytes()).hexdigest()
  assert copied==hashlib.sha256(original.read_bytes()).hexdigest(),relative
  hashes[str(relative)]=copied
(out/'isolated-build-source-manifest.json').write_text(json.dumps({'source_directory':str(root/'app'),'build_directory':str(workspace/'app'),'files':hashes,'exclusions':['node_modules','.next','.env*','*.tsbuildinfo']},ensure_ascii=False,indent=2))
print('Isolated build source files verified:',len(hashes))
print(workspace/'app')
