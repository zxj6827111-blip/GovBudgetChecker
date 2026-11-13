from pathlib import Path
import datetime,json,os,socket,subprocess,sys,time,urllib.request,signal
ROOT=Path(__file__).resolve().parents[2]; OUT=Path(__file__).resolve().parent
with socket.socket() as s:
 s.bind(('127.0.0.1',0)); port=s.getsockname()[1]
env=os.environ.copy(); env.update(BACKEND_URL='http://127.0.0.1:1',GOVBUDGET_API_KEY='ephemeral-ui-contract',BACKEND_API_KEY='ephemeral-ui-contract',E2E_BASE_URL=f'http://127.0.0.1:{port}',E2E_EXTERNAL_SERVER='1')
env.pop('CI',None); server=None
receipt={'scope':'real browser and production frontend with intercepted API contract fixtures; no production DB; not human visual/UAT acceptance','excluded_contract':'/e2e/batch-upload CSP demo requires an enabled development fixture; production build intentionally keeps it closed','started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'backend_forwarding':'127.0.0.1:1 unreachable'}
with (OUT/'browser_server_final_delivery.log').open('w') as log:
 try:
  server=subprocess.Popen(['node','node_modules/next/dist/bin/next','start','--hostname','127.0.0.1','--port',str(port)],cwd=ROOT/'app',env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
  for i in range(60):
   try:
    urllib.request.urlopen(f'http://127.0.0.1:{port}/login',timeout=2);break
   except Exception:
    if server.poll() is not None: raise RuntimeError('isolated frontend exited')
    time.sleep(1)
  else:raise RuntimeError('isolated frontend not ready')
  specs=['archive-page','material-ledger','material-ledger-detail','review-lifecycle','obligation-review','workspace-navigation','quality-management','security-headers']
  cmd=['node','app/node_modules/@playwright/test/cli.js','test','--config',str(OUT/'browser.playwright.config.ts'),'--workers','2','--grep-invert','demo workbench renders under CSP without violations','--reporter','json','--output',str(OUT/'browser_artifacts_final_delivery')]+['e2e/tests/'+p+'.spec.ts' for p in specs]
  with (OUT/'browser_result_final_delivery.log').open('w') as result:
   p=subprocess.run(cmd,cwd=ROOT,env=env,stdout=result,stderr=subprocess.STDOUT,timeout=900)
  receipt.update(exit_code=p.returncode,status='passed' if p.returncode==0 else 'failed',specs=specs)
 except Exception as e: receipt.update(status='error',error=str(e))
 finally:
  if server is not None and server.poll() is None:
   os.killpg(server.pid,signal.SIGTERM)
   try:server.wait(timeout=15)
   except subprocess.TimeoutExpired:os.killpg(server.pid,signal.SIGKILL);server.wait(timeout=10)
  receipt['cleanup']='complete' if server is None or server.poll() is not None else 'failed'
  (OUT/'browser_receipt_final_delivery.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
  print(json.dumps(receipt,ensure_ascii=False))
sys.exit(0 if receipt.get('status')=='passed' and receipt.get('cleanup')=='complete' else 1)
