"""不改全量测试；等待它依赖的真实任务结束后原样运行并保存退出码。"""
import hashlib,json,os,subprocess,time
from pathlib import Path
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[1];WORK=ROOT/'artifacts/comprehensive-review-closure-20261109'
path=ROOT/'uploads/79efdd8f0d7b3a2938de65cfb5aad101/status.json';before=hashlib.sha256(path.read_bytes()).hexdigest();assert json.loads(path.read_text())['status'] not in {'queued','processing','running','analyzing'}
manifest=json.loads((OUT/'release_final11_code_manifest.json').read_text());assert all(hashlib.sha256((WORK/n).read_bytes()).hexdigest()==sha for n,sha in manifest.items())
env=dict(os.environ,PYTHON_DOTENV_DISABLED='1')
with (OUT/'backend-final11-release.log').open('w') as log:
 result=subprocess.run([str(WORK/'.venv/bin/python'),'-m','pytest','-q'],cwd=WORK,env=env,stdout=log,stderr=subprocess.STDOUT)
assert hashlib.sha256(path.read_bytes()).hexdigest()==before,'Test sampled a changing live record again'
receipt=dict(exit_code=result.returncode,live_dependency_stable=True,source_matches_frozen_snapshot=True,original_test_unchanged=True)
(OUT/'backend_final11_test_receipt.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt),flush=True)
