from pathlib import Path
import datetime,json,os,socket,subprocess,sys,time,uuid
ROOT=Path(__file__).resolve().parents[2];OUT=Path(__file__).resolve().parent
name='gbc-r16-pg-'+uuid.uuid4().hex[:10]
receipt={'container':name,'scope':'disposable PostgreSQL test container only','started_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
try:
 subprocess.run(['docker','run','-d','--name',name,'-e','POSTGRES_USER=gbc_test','-e','POSTGRES_PASSWORD=local_ephemeral_test','-e','POSTGRES_DB=gbc_round16','-p','127.0.0.1::5432','postgres:16'],check=True,capture_output=True,text=True,timeout=60)
 for _ in range(30):
  ready=subprocess.run(['docker','exec',name,'pg_isready','-U','gbc_test','-d','gbc_round16'],capture_output=True,text=True,timeout=5)
  if ready.returncode==0:break
  time.sleep(1)
 else:raise RuntimeError('test PostgreSQL did not become ready')
 info=json.loads(subprocess.check_output(['docker','inspect',name],text=True))[0]
 port=info['NetworkSettings']['Ports']['5432/tcp'][0]['HostPort']
 receipt['image_id']=info['Image'];receipt['postgres_version']=subprocess.check_output(['docker','exec',name,'postgres','--version'],text=True).strip()
 env=os.environ.copy();env['PYTHON_DOTENV_DISABLED']='1';env['GOVBUDGET_TEST_DATABASE_URL']=f'postgresql://gbc_test:local_ephemeral_test@127.0.0.1:{port}/gbc_round16';env.pop('DATABASE_URL',None)
 start=time.monotonic()
 with (OUT/'pytest_postgres_verified.log').open('w') as log:
  r=subprocess.run([sys.executable,'-m','pytest','-m','real_database','-q'],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=1800)
 receipt.update(exit_code=r.returncode,elapsed_seconds=round(time.monotonic()-start,2),status='passed' if r.returncode==0 else 'failed')
except Exception as exc:
 receipt.update(status='error',error=f'{type(exc).__name__}: {exc}')
finally:
 try:
  clean=subprocess.run(['docker','rm','-f',name],capture_output=True,text=True,timeout=30)
  probe=subprocess.run(['docker','inspect',name],capture_output=True,text=True,timeout=10)
  receipt['cleanup']='complete' if clean.returncode==0 and probe.returncode!=0 else 'failed'
 except Exception as exc:receipt.update(cleanup='failed',cleanup_error=type(exc).__name__)
 (OUT/'postgres_test_verified_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps(receipt,ensure_ascii=False),flush=True)
sys.exit(0 if receipt.get('status')=='passed' and receipt.get('cleanup')=='complete' else 1)
