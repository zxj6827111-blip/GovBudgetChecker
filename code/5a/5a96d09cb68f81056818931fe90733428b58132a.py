"""Narrow residual checks of the same scope and attribution requirements."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker')
CODE=ROOT.parent/'gbc-worktrees/auto-check-remediation-r22'
OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(CODE))
from src.engine.narrative_zero_evidence import narrative_zero_sources
from src.services.remediation_statistics import derive_attribution
def load(name,p):
    spec=importlib.util.spec_from_file_location(name,p)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod
tool=load('r3_carrier',CODE/'scripts/verify_three_carriers.py')
collector=load('r3_collector',CODE/'scripts/phase0_r22_target_relations.py')
results={}
results['bare_frame_declaration']=[]
for declaration in ['以下为模板示例。','模板示例：','示例：']:
    text='甲局2024年度部门决算。'+declaration+'财政拨款公务接待费支出决算0万元。因公出国（境）费支出决算0万元。'
    d=SimpleNamespace(page_texts=[text],fact_context=SimpleNamespace(org_name='甲局',fiscal_year=2024),report_kind='final')
    results['bare_frame_declaration'].append({'text':text,'expected_authorized':False,'actual_authorized':{i:bool(narrative_zero_sources(d,table_code='FIN_07_three_public',indicator=i,stage='决算')) for i in ['reception','overseas']}})

before={'targets':[{'job_uuid':'same','rules':{'V33-X':{'status':'pass','relations':[{'relation_id':'r1','status':'pass'}]}}}]}
after={'targets':[{'job_uuid':'same','rules':{'V33-X':{'status':'insufficient_data','relations':[{'relation_id':'r1','status':'insufficient_data'}]}}}]}
(OUT/'regression_before.json').write_text(json.dumps(before))
(OUT/'regression_after.json').write_text(json.dumps(after))
with contextlib.redirect_stdout(io.StringIO()):
    collector._run_relations_stats(str(OUT/'regression_before.json'),str(OUT/'regression_after.json'),str(OUT/'regression_stats.json'))
results['before_pass_after_unknown']=json.loads((OUT/'regression_stats.json').read_text())
actual=json.loads((ROOT/'outputs/r22_rework5_20261107/three_dimension_stats.json').read_text())
results['actual_unknown_historical_only']=[r for r in actual['relations'] if r['engine_dimension']=='unknown' and r['attributions']==['resolved_before_this_plan']]

# Component boundary: a formal comparison entry needs input identity, this helper
# currently compares finding snapshots only. Do not treat this as a zero-regression pass.
same={'checksum':'a'*64,'analysis_basis_token':'generation-a','result':{'rule_findings':[]},'review_report':{'issues':[],'check_receipts':[]}}
different={**same,'checksum':'b'*64,'analysis_basis_token':'generation-b'}
for name,payload in [('identity_a.json',same),('identity_b.json',different)]:
    (OUT/name).write_text(json.dumps(payload))
buf=io.StringIO()
with contextlib.redirect_stdout(buf):tool.compare(OUT/'identity_a.json',OUT/'identity_b.json')
results['comparison_identity_boundary']=json.loads(buf.getvalue())

(OUT/'residual_results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(results,ensure_ascii=False,indent=2))
