"""Finite read-only check of actual FIN_02 column-number facts and saved correction."""
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
os.environ.update(PYTHON_DOTENV_DISABLED="1", DATABASE_URL="", AI_ENABLED="false", OCR_ENABLED="false")
import dotenv
dotenv.load_dotenv = lambda *a, **k: False
dotenv.dotenv_values = lambda *a, **k: {}
import pymupdf
from src.engine.fact_context import FactContext, resolve_operand

OUT = Path(__file__).resolve().parent
BASE = ROOT / "outputs/remediation_20261006_round13/services_final/original_mixed_final"
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
files = ["src/services/fiscal_fact_materializer.py", "src/engine/fact_context.py",
         "scripts/run_isolated_service_validation.py"]
before = {name: sha(ROOT/name) for name in files}
data = json.loads((BASE/"fact_context.json").read_text())
context = FactContext.from_dict(data)
header_rows = [row for row in data["facts"] if row["table_code"] == "FIN_02_income" and row["subject_name"] == "栏次"]
header_ids = {row["fact_uid"] for row in header_rows}
definite_ids = {row.fact_uid for row in context.definite_facts()}
report = json.loads((BASE/"report.json").read_text())
service = json.loads((BASE.parent/"service_receipt.json").read_text())
scenario = next(row for row in service["scenarios"] if row["scenario"] == "original_mixed_final")

# Reproduce ONLY the finite selector on the actual saved fact list.
# Do not perform the correction API request or mutate any existing fact.
selector_candidates = [f for f in data["facts"] if f.get("raw_value") and f.get("evidence_ids")]
selector_candidates.sort(key=lambda f: not any(r.get("source_method")=="ocr" for r in f.get("extra_dims",{}).get("evidence_refs",[])))
selected = selector_candidates[0]
corrected = FactContext.from_dict(json.loads((BASE/"corrected/fact_context.json").read_text()))
recorded_target = scenario["correction"]["target_id"]
resolutions = []
for name, kwargs in [("unscoped", {}), ("production_total_role", {"subject_tokens":("合计","总计")})]:
    result = resolve_operand(context,"total_actual",table_code="FIN_02_income",report_year=2024,budget_stages=("决算",),**kwargs)
    resolutions.append({"name":name,"status":result.status,"value":str(result.value),"fact_uid":getattr(result.fact,"fact_uid",None),
                        "selected_header":getattr(result.fact,"fact_uid",None) in header_ids})
with pymupdf.open(BASE/"source.pdf") as pdf:
    native_p7 = pdf[6].get_text()
rootline = {}
for filename, nums in {
    "src/services/fiscal_fact_materializer.py":[184,185,189,200,201,204,205,224,239,265,273,277,278,293,301,311,312,313,314,315,316,478,588,595,597,599],
    "src/engine/fact_context.py":[35,36,158,159,160,170,172,173,293,295],
    "scripts/run_isolated_service_validation.py":[478,479,482,484,487,500,503,504,505,507,515,517,519],
}.items():
    lines=(ROOT/filename).read_text().splitlines()
    rootline[filename]=[{"line":n,"text":lines[n-1]} for n in nums]
actual_consumers={name:[row for row in report["review_report"][name]
                        if header_ids.intersection(row.get("operand_fact_ids",row.get("fact_ids",[])))]
                  for name in ["check_receipts","issues"]}
after={name:sha(ROOT/name) for name in files}
result={"scope":"actual_original_mixed_final_finite_header_check; no new rules/neighbors/service",
        "source_sha_before":before,"source_sha_after":after,"source_unchanged":before==after,
        "saved_original_sha256":sha(BASE/"source.pdf"),"job_id":scenario["job_id"],
        "header_facts":header_rows,"header_definite_membership":{uid:uid in definite_ids for uid in sorted(header_ids)},
        "native_page7_text":native_p7,"actual_total_facts":[row for row in data["facts"] if row["table_code"]=="FIN_02_income" and row["subject_name"]=="合计"],
        "selector_replay":{"selected_uid":selected["fact_uid"],"selected_subject":selected["subject_name"],"selected_raw":selected["raw_value"],
                           "computed_overlay_raw":str(float(str(selected["raw_value"]).replace(",", "").replace(" ",""))+10),
                           "matches_saved_correction_target":selected["fact_uid"]==recorded_target},
        "saved_correction":scenario["correction"],
        "persisted_corrected_header":corrected.fact_by_uid(recorded_target).to_dict(),
        "operand_resolution_controls":resolutions,"actual_header_consumers":actual_consumers,"rootline":rootline,
        "assessment":"Confirmed native fact pollution: five FIN_02 column-number entries are included by definite_facts. The generic validation selector chooses the first raw/evidence fact (OCR preference only), and the real saved correction changed 栏次 1 to 11.0. This is a workflow injection, not an amount correction. The observed production total-role resolver selects actual 合计746.62; no saved issue/check receipt names any of these five header IDs as an operand. Do not infer deterministic wrong calculation or omission from this probe.",
        "root_cause":"Native materializer starts data at the first row with any numeric measure (265-278), does not exclude 栏次/is_header before classification/numeric materialization (189-239), accepts arbitrary nonnumeric label, and marks these native entries unverified (595-599). definite_facts includes unverified (fact_context36/295).",
        "limitations":["Current formal evidence verification remains a separate gate; definite_facts membership is not proof of verified evidence.",
                       "Only stored original/corrected snapshots and real source P7 were checked; no new API/OCR/service call or claim of monetary relation change."],
        "reproduction":{"command":"PYTHONDONTWRITEBYTECODE=1 .venv/bin/python outputs/reacceptance_20261006_round14/rules_audit/header_probe.py",
                         "code":Path(__file__).read_text()}}
(OUT/"header_probe.json").write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str)+"\n")
print(json.dumps({"header_facts":len(header_rows),"definite_headers":len(header_ids & definite_ids),
                  "selected":selected["fact_uid"],"selected_raw":selected["raw_value"],
                  "corrected_raw":corrected.fact_by_uid(recorded_target).raw_value,
                  "actual_header_consumers":{k:len(v) for k,v in actual_consumers.items()},
                  "source_unchanged":before==after,"header_probe_sha256":sha(OUT/"header_probe.json")},ensure_ascii=False))
