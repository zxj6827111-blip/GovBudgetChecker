import os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
os.environ.update(PYTHON_DOTENV_DISABLED='1',DATABASE_URL='',GOVBUDGET_TEST_DATABASE_URL='',AI_ENABLED='false',AI_ASSIST_ENABLED='false',OCR_ENABLED='false')
import json, contextlib, io
from pathlib import Path
import dotenv
dotenv.load_dotenv=lambda *a,**k:False
dotenv.dotenv_values=lambda *a,**k:{}
audit=json.loads(Path('outputs/reacceptance_20261005_round10/rules_audit.json').read_text())
scope={'__file__':str(Path('outputs/reacceptance_20261005_round8/root_business_neighbors.py').resolve()),'__name__':'__audit_replay__'}
with contextlib.redirect_stdout(io.StringIO()) as out:
 exec(compile(audit['reproduction']['original_replay_code'],scope['__file__'],'exec'),scope)
original=json.loads(out.getvalue())
exec(compile(audit['reproduction']['executable_neighbor_code'],'<round10_rules_audit>','exec'),scope)
print(json.dumps({'original_9_cases':len(original),'original_cases':original,'amount_boundary_cases':scope['amount_cases'],'neighbors':[{'case_id':c['case_id'],'expected':c['expected_relation_status'],'actual':c['actual_relation_status'],'findings':c['percentage_issues']} for c in scope['cases']]},ensure_ascii=False,default=str))
