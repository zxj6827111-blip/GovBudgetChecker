import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
from urllib.parse import urlparse,unquote
import sys
import os

ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker')
OUT=Path(__file__).resolve().parent
env=json.loads((OUT/'private/effective_environments.json').read_text())['api']
url=urlparse(env['DATABASE_URL'])
dump=OUT/'private/database_before.dump'
with dump.open('wb') as f:
 result=subprocess.run(['docker','exec','fiscal_postgres','pg_dump','-U',unquote(url.username),'-d',unquote(url.path.lstrip('/')),'--format=custom'],stdout=f,stderr=subprocess.PIPE)
dump.chmod(0o600)
if result.returncode:raise RuntimeError('pg_dump failed: '+result.stderr.decode()[:300])
archive=OUT/'private/data_before.tar.gz'
with tarfile.open(archive,'w:gz') as t:t.add(ROOT/'data',arcname='data')
archive.chmod(0o600)
config=OUT/'private/root.env.before';config.write_bytes((ROOT/'.env').read_bytes());config.chmod(0o600)
originals=[{'path':str(p.relative_to(ROOT)),'size':p.stat().st_size,'mtime_ns':p.stat().st_mtime_ns} for p in (ROOT/'uploads').glob('*/*.pdf')]
(OUT/'original_pdf_manifest_before.json').write_text(json.dumps(originals,ensure_ascii=False,indent=2))
safe={'database_dump_bytes':dump.stat().st_size,'database_dump_sha256':hashlib.sha256(dump.read_bytes()).hexdigest(),
      'data_archive_bytes':archive.stat().st_size,'original_pdfs':len(originals)}
(OUT/'backup_receipt.json').write_text(json.dumps(safe,indent=2));print(json.dumps(safe))
