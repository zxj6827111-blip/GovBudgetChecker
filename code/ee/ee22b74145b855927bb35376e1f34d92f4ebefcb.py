"""安全复查两处修复完成后，只读复制已知源码；冻结后禁止继续编辑。"""
import hashlib,json,shutil,subprocess
from pathlib import Path
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[1];WORK=ROOT/'artifacts/comprehensive-review-closure-20261109';TARGET=OUT/'release_final8_code'
expected={'src/engine/native_empty_operands.py','src/services/issue_workflow_store.py','tests/remediation/test_certified_empty_money_table.py','tests/remediation/test_decision_source_identity.py'}
changed=set(subprocess.check_output(['git','diff','--name-only'],cwd=WORK,text=True).splitlines());assert changed==expected,changed
assert not TARGET.exists();TARGET.mkdir()
paths=subprocess.check_output(['git','ls-files','-z'],cwd=WORK).decode().split('\0');manifest={}
for name in paths:
 if not name:continue
 source=WORK/name;target=TARGET/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
 manifest[name]=hashlib.sha256(target.read_bytes()).hexdigest()
(OUT/'release_final8_code_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2));print('Frozen files',len(manifest),'changes',len(changed),flush=True)
