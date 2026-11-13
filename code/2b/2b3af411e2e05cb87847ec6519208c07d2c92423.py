"""Observe current gate locals without replacing application or gate code."""
from pathlib import Path
import importlib.util,json,sys
ROOT=Path(__file__).resolve().parents[2];OUT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('gate_export_observation',ROOT/'scripts/run_zc07_acceptance.py')
gate=importlib.util.module_from_spec(spec);spec.loader.exec_module(gate)
observed=[]
def trace(frame,event,arg):
 if event=='return' and frame.f_code.co_name=='_evaluate_export_agreement':
  found=frame.f_locals.get('found',{});j=found.get('.json',[]);c=found.get('.csv',[])
  if len(j)==len(c)==1:
   differences={k:{'json':j[0][k],'csv':c[0].get(k)} for k in j[0] if j[0][k]!=c[0].get(k)}
   observed.append({'result':arg,'canonical_differences':differences})
 return trace
p=OUT/'export_probe/02_current'
manifest={'documents':[{'doc_id':'synthetic-production-02','export_paths':[str(p/('report'+s)) for s in ['.json','.csv','.docx','.pdf']]}]}
sys.settrace(trace)
try:gate._evaluate_export_agreement(manifest,OUT)
finally:sys.settrace(None)
(OUT/'export_signature_diagnosis.json').write_text(json.dumps(observed,ensure_ascii=False,indent=2))
print('Observed canonical differences:',list(observed[0]['canonical_differences']))
