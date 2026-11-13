from pathlib import Path
import json,time
from src.schemas.parse_artifact import ParseArtifact
from src.engine.fact_context import FactContext
from src.services.fiscal_input_builder import FiscalInputBuilder
base=Path("/Volumes/workSSD/software development/GovBudgetChecker/artifacts/pdf-input-v2-20261018/frozen34-delivery")
out=Path("/Volumes/workSSD/software development/GovBudgetChecker/artifacts/pdf-input-v2-20261018-joint")
results=[]
fields=["raw_value","normalized_value","unit","org_name","org_scope","fiscal_year","period","fund_scope","budget_stage","subject_code","subject_name","indicator","table_code","instance_uid","row_order","recognition_status"]
for path in sorted(base.glob("*/documents/*/artifact.json")):
    start=time.monotonic()
    artifact=ParseArtifact.from_dict(json.loads(path.read_text()))
    old=FactContext.from_dict(json.loads(path.with_name("fact_context.json").read_text()))
    context=FiscalInputBuilder().facts(artifact,org_name=old.org_name,fiscal_year=old.fiscal_year,report_kind=old.report_kind)
    before={f.fact_uid:f.to_dict() for f in old.facts}; after={f.fact_uid:f.to_dict() for f in context.facts}
    changes=[]
    for uid in sorted(set(before)|set(after)):
        a,b=before.get(uid,{}),after.get(uid,{})
        diff={field:[a.get(field),b.get(field)] for field in fields if a.get(field)!=b.get(field)}
        if diff: changes.append({"fact_uid":uid,"diff":diff})
    r={"document":str(path.parent.relative_to(base)),"before":len(before),"after":len(after),"changes":changes,"row_diagnostics":artifact.reconstruction_diagnostics,"seconds":round(time.monotonic()-start,3)}
    results.append(r)
    (out/"joint-frozen-comparison.json").write_text(json.dumps(results,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in r.items() if k not in {"changes","row_diagnostics"}},ensure_ascii=False),flush=True)
print("changes",sum(len(r["changes"]) for r in results),flush=True)
