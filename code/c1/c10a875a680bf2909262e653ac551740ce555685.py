"""冻结最后原件续页角色修复及同请求历史快照复用，不包含任何运行数据。"""
import hashlib,json,shutil,subprocess
from pathlib import Path
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[1];WORK=ROOT/'artifacts/comprehensive-review-closure-20261109';TARGET=OUT/'release_final12_code'
new='tests/remediation/test_orphan_functional_roots.py'
expected={'src/engine/native_hierarchy_checks.py','src/engine/native_issue_dedup.py',new}
changed=set(subprocess.check_output(['git','diff','--name-only'],cwd=WORK,text=True).splitlines())|{new};assert changed==expected,changed
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=WORK,text=True).strip().startswith('01bfb5c5')
assert not TARGET.exists();TARGET.mkdir()
paths=set(subprocess.check_output(['git','ls-files','-z'],cwd=WORK).decode().split('\0'));paths.discard('');paths.add(new);manifest={}
for name in sorted(paths):
 source=WORK/name;target=TARGET/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
 manifest[name]=hashlib.sha256(target.read_bytes()).hexdigest()
(OUT/'release_final12_code_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2));print('Frozen files',len(manifest),'changes',len(changed),flush=True)
