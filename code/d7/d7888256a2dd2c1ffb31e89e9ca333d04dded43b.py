"""受限范围发布备份：原配置只读，数据库及五任务历史只写本报告私有目录。"""
import json,pathlib,subprocess,urllib.parse,hashlib,tarfile
ROOT=pathlib.Path('/Volumes/workSSD/software development/GovBudgetChecker');OUT=ROOT/'outputs/native_review_coverage_20261108';PRIVATE=OUT/'private';PRIVATE.mkdir(mode=0o700,exist_ok=True);PRIVATE.chmod(0o700)
env=json.loads((ROOT/'outputs/claude_closeout_20261108/private/effective_environments.json').read_text())['api'];url=urllib.parse.urlparse(env['DATABASE_URL']);dump=PRIVATE/'database_before.dump'
with dump.open('wb') as f:
 r=subprocess.run(['docker','exec','fiscal_postgres','pg_dump','-U',urllib.parse.unquote(url.username),'-d',urllib.parse.unquote(url.path.lstrip('/')),'--format=custom'],stdout=f,stderr=subprocess.PIPE)
dump.chmod(0o600)
if r.returncode:raise RuntimeError('Database backup failed; deployment not started')
manifest=json.loads((OUT/'manifest_before.json').read_text());archive=PRIVATE/'five_jobs_before.tar.gz'
with tarfile.open(archive,'w:gz') as tar:
 for job in manifest:
  status=json.loads((ROOT/'uploads'/job/'status.json').read_text())
  if status['status']=='review_required':tar.add(ROOT/'uploads'/job,arcname='uploads/'+job)
 tar.add(ROOT/'data',arcname='data')
archive.chmod(0o600)
receipt={'database_dump_bytes':dump.stat().st_size,'database_dump_sha256':hashlib.sha256(dump.read_bytes()).hexdigest(),'history_archive_bytes':archive.stat().st_size,'history_archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}
(OUT/'backup_receipt.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt),flush=True)
