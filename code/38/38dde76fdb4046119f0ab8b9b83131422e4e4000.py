from pathlib import Path
import shutil,hashlib,json
base=Path(__file__).resolve().parent
runtime=base/'integrated-runtime-v1'
runtime.mkdir(exist_ok=False)
for name in ('src','api','ocr_service'):
    shutil.copytree(base/'implementation'/name,runtime/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
manifest={str(p.relative_to(runtime)):hashlib.sha256(p.read_bytes()).hexdigest() for p in runtime.rglob('*') if p.is_file()}
(base/'integrated-runtime-v1-sha256.json').write_text(json.dumps(manifest,indent=2))
print(len(manifest))
