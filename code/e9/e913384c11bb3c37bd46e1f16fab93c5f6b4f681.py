import json,subprocess,sys
from pathlib import Path
ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker');OUT=ROOT/'outputs/pdf_acceptance_shanghai2022_20261021';items=[]
for p in (OUT/'runtime_native').glob('*/immutable_snapshot.json'):
 if p.parent.name=='native_correction':continue
 s=json.loads(p.read_text());truth={'source_file_hash':s['source_file_hash'],'split':'independent_acceptance','review_status':'draft','production_output_used_as_truth':False,'annotation_complete':False,'report_kind':'budget','material_group':'native','region':'上海市','year':2022,'cells':[],'note':'No human cell truth supplied. This intentionally empty draft is a gate-input completeness test; it is not an accuracy denominator.'}
 t=OUT/'human_truth_pending'/f"{s['source_file_hash']}.json";t.parent.mkdir(exist_ok=True);t.write_text(json.dumps(truth,ensure_ascii=False,indent=2));items.append({'truth':str(t),'snapshot':str(p),'artifact':str(p.parent/'artifact.json')})
manifest=OUT/'human_truth_pending/manifest.json';manifest.write_text(json.dumps({'items':items},ensure_ascii=False,indent=2))
result=subprocess.run([sys.executable,str(ROOT/'scripts/verify_pdf_cell_acceptance.py'),'--manifest',str(manifest),'--output',str(OUT/'formal_gate_result.json')],capture_output=True,text=True)
(OUT/'formal_gate.log').write_text(result.stdout+result.stderr);(OUT/'formal_gate_execution.json').write_text(json.dumps({'exit_code':result.returncode,'expected_exit_code':2,'intentionally_draft_not_human_truth':True,'materials_with_snapshots':len(items)},indent=2));print(result.stdout[-1000:]);print('exit',result.returncode)
