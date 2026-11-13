"""将独立原页对照结果与真实 Postgres 回读逐格关联，含缓存后的同一来源。"""
import json,re
from pathlib import Path
BASE=Path(__file__).resolve().parent


def obj(value):return json.loads(value) if isinstance(value,str) else value

def key(value):return re.sub(r"\s+","",str(value or ""))

def main():
 receipt=json.loads((BASE/"service/service_receipt.json").read_text())
 assert receipt["status"]=="passed" and receipt["cleanup"]=="complete" and receipt["source_unchanged_during_run"]
 original=json.loads((BASE/"original_verification.json").read_text())
 target_hashes={r["sha256"] for r in original["results"] if r["repaired_money_cells"]}
 observations=[];first_counts={}
 for scenario in receipt["scenarios"]:
  if scenario["source_sha256"] not in target_hashes:continue
  expected=json.loads((BASE/"originals"/scenario["source_sha256"]/"comparison.json").read_text())
  for version in sorted((BASE/"service_database"/scenario["job_id"]).iterdir()):
   artifact=json.loads((version/"artifact.json").read_text());facts=json.loads((version/"facts.json").read_text())
   ingest=json.loads((version/"ingest.json").read_text())
   evidence_by_id={e["evidence_id"]:e for e in json.loads((version/"evidence.json").read_text())}
   assert artifact["parser_fingerprint"]=="pdfplumber-cells-v23-native-structure-recovery"
   for target in expected:
    matched=[]
    for fact in facts:
     extras=obj(fact["extra_dims"])
     if fact["subject_code"]==target["subject_code"] and key(fact["subject_name"])==target["subject_name"] and fact["indicator"]==target["indicator"]:
      refs=extras["evidence_refs"]
      if any(r["physical_page"]==target["physical_page"] for r in refs):matched.append((fact,refs))
    assert len(matched)==1,(scenario["scenario"],target)
    fact,refs=matched[0];ref=next(r for r in refs if r["physical_page"]==target["physical_page"])
    assert fact["raw_value"]==target["expected_amount"] and ref["quote"]==target["actual_amount"]
    assert ref["source_cell_id"]==target["source_cell_id"] and ref["bbox"]==target["bbox"]
    assert ref["file_hash"]==scenario["source_sha256"]
    assert ref["source_provenance"]["native_text_sources"]
    stored=evidence_by_id[ref["evidence_id"]]
    assert stored["quote"]==ref["quote"] and stored["bbox"]==ref["bbox"]
    assert obj(stored["source_provenance"])==ref["source_provenance"]
    assert fact["recognition_status"]=="unverified"
   observations.append(dict(scenario=scenario["scenario"],source_sha256=scenario["source_sha256"],analysis_revision=version.name,
                            repaired_cells_verified=len(expected),parse_run_id=ingest["parse_run_id"]))
   first_counts.setdefault(scenario["source_sha256"],len(expected))
 correction=next(s for s in receipt["scenarios"] if s["scenario"]=="native_correction")
 assert correction["correction_validation_passed"] and correction["correction"]["cache_readback_validation_passed"]
 assert correction["correction"]["original_parse_basis_preserved"]
 assert sum(first_counts.values())==19
 result=dict(status="passed",unique_originals=len(first_counts),unique_repaired_money_cells=19,
             snapshots_verified=len(observations),cache_and_history_verified=True,observations=observations)
 (BASE/"service_readback_verification.json").write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False))


if __name__=="__main__":main()
