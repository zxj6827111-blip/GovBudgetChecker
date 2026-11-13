from pathlib import Path
root=Path('/Volumes/workSSD/software development/GovBudgetChecker')
out=root/'outputs/pdf_acceptance_shanghai2022_20261021'
s=(root/'scripts/run_isolated_service_validation.py').read_text()
s=s.replace('ROOT = Path(__file__).resolve().parents[1]',f'ROOT = Path({str(root)!r})\nsys.path.insert(0,str(ROOT))\nimport shutil')
start=s.index('                budget = args.budget_pdf')
end=s.index('                receipt["expected_scenario_count"]',start)
s=s[:start]+'''                inventory=json.loads((out.parent/'source_manifest.json').read_text())
                pilots=['上海市交通委员会.pdf','上海市商务委员会.pdf','上海市农场管理局.pdf',
                        '上海市人民政府研究室.pdf','上海化学工业区管理委员会.pdf',
                        '上海市水务局（上海市海洋局）.pdf','上海推进科技创新中心建设办公室.pdf']
                order=sorted(inventory,key=lambda r:(pilots.index(r['file']) if r['file'] in pilots else 99,r['file']))
                scenes=[('native_final' if i==0 else 'native_budget' if i==2 else r['doc_id'],Path(r['frozen_path']),False,False)
                        for i,r in enumerate(order)]
                scenes.append(('native_correction',Path(order[0]['frozen_path']),False,False))
                receipt['pilot_files']=pilots
                receipt['scope']='new_shanghai2022_material_runtime_acceptance_no_human_truth'
                receipt['scene_labels_note']='native_budget is legacy harness label for farm 2022 final account; not a budget sample'
''' +s[end:]
# Preserve the actual immutable snapshot and parsed artifact before corrections, not only current context.
needle='                    fact_snapshot = work / "uploads" / job_id / "fact_context.json"'
s=s.replace(needle,'''                    (target/'immutable_snapshot.json').write_bytes(frozen_facts_path.read_bytes())
                    from src.db.connection import DatabaseConnection
                    async def persist_artifact():
                        from src.services.parse_artifact_store import load_parse_artifact
                        import asyncpg
                        conn=await asyncpg.connect(dsn)
                        try:
                            artifact=await load_parse_artifact(conn,structured_ingest_from_payload(payload)['parse_run_id'])
                            (target/'artifact.json').write_text(json.dumps(artifact.to_dict(),ensure_ascii=False))
                        finally: await conn.close()
                    asyncio.run(persist_artifact())
''' +needle)
# Isolate each material failure, continue full batch, preserve checkpoint and original responses.
start=s.index('                    scenario_started = time.monotonic()')
end=s.index('                if args.history_rebuild_validation:',start)
body=s[start:end]
body=body.replace('                    receipt["scenarios"].append(scenario)','                    receipt["scenarios"].append(scenario)\n                    (out/"live_receipt.json").write_text(json.dumps(receipt,ensure_ascii=False,indent=2))')
s=s[:start]+'''                    job_id=None
                    try:
'''+''.join('    '+ln if ln.strip() else ln for ln in body.splitlines(True))+'''                    except Exception as exc:
                        failure={'scenario':label,'file':str(pdf),'source_sha256':sha(pdf),'job_id':job_id,
                                 'error_type':type(exc).__name__,'error':str(exc),'service_validation_passed':False,
                                 'elapsed_seconds':round(time.monotonic()-scenario_started,2)}
                        receipt['scenarios'].append(failure)
                        (out/label).mkdir(exist_ok=True)
                        (out/label/'first_failure.json').write_text(json.dumps(failure,ensure_ascii=False,indent=2))
                        print(json.dumps(failure,ensure_ascii=False),flush=True)
                    finally:
                        (out/'live_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
                        if job_id and (work/'uploads'/job_id).exists():
                            shutil.copytree(work/'uploads'/job_id,out/'runtime_uploads'/job_id,dirs_exist_ok=True)
                    if len(receipt['scenarios'])==7:
                        (out/'pilot_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
                        native_pilot=receipt['scenarios'][:6]
                        if not all(x.get('structured_ingest_validation',{}).get('passed') for x in native_pilot):
                            receipt['pilot_chain_defects']='Preserved; continue other documents to determine scope without production logic changes'
''' +s[end:]
# Restrict history controls to three originals, preserving first execution artifacts.
s=s.replace("jobs=[s['job_id']for s in receipt['scenarios']],","jobs=[s['job_id']for s in receipt['scenarios'][:3] if s.get('service_validation_passed')],")
# Persist schema and dump before disposable catalog removal even when later controls fail.
s=s.replace('        try:\n            pg_cleanup =', '''        try:
            dump=subprocess.run(['docker','exec',name,'pg_dump','-U','validator','-d','validation','-Fc'],capture_output=True)
            if dump.returncode==0:
                (out/'acceptance_database.dump').write_bytes(dump.stdout)
                receipt['database_dump_sha256']=sha(out/'acceptance_database.dump')
            schema=subprocess.run(['docker','exec',name,'psql','-U','validator','-d','validation','-Atc','SELECT version FROM schema_migrations ORDER BY version'],capture_output=True,text=True)
            (out/'applied_migrations.txt').write_text(schema.stdout)
            if 'work' in locals() and work.exists():
                shutil.copytree(work/'uploads',out/'runtime_uploads',dirs_exist_ok=True)
        except Exception as preserve_error:
            receipt['errors'].append('preservation: '+str(preserve_error))
        try:
            pg_cleanup =''')
# The original TemporaryDirectory removes work before outer finally; copy/dump immediately within context too.
needle='            receipt["resource_snapshot"] = docker('
s=s.replace(needle,'''            shutil.copytree(work/'uploads',out/'runtime_uploads',dirs_exist_ok=True)
            dump=subprocess.run(['docker','exec',name,'pg_dump','-U','validator','-d','validation','-Fc'],capture_output=True,check=True)
            (out/'acceptance_database.dump').write_bytes(dump.stdout)
''' +needle)
(out/'harness/run_batch.py').write_text(s)
compile(s,str(out/'harness/run_batch.py'),'exec')
