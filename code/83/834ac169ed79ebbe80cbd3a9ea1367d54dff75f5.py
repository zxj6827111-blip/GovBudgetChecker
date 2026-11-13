"""固定原件的 source-first 逐格核对及既有原生数值保全；不是未知材料盲测。"""
from collections import Counter
import hashlib,importlib.util,json,re,sys
from pathlib import Path
import pymupdf
BASE=Path(__file__).resolve().parent;ROOT=BASE.parents[1];sys.path.insert(0,str(ROOT))
from src.services.pdf_parser import PDFParser,PARSER_FINGERPRINT
from src.services.table_reading_assessment import assess_table_reading
from src.services.table_import_receipt import build_table_import_receipt
from src.services.document_profile_resolver import resolve_document_profile
from src.engine.fact_context import build_fact_context_from_artifact,operand_rejection_reason
from src.schemas.parse_artifact import ParseArtifact
from tests.remediation.test_native_structure_recovery import EXPECTED


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def key(text):return re.sub(r"\s+","",str(text or ""))
def values(artifact):
 return Counter((c.page_number,c.col_idx,tuple(c.bbox or ()),c.raw_text,c.numeric_value)
                for t in artifact.physical_fragments for c in t.cells if c.numeric_value is not None and not c.is_header)


def main():
 files={str(p.relative_to(ROOT)):sha(p) for directory in ["src","api"] for p in (ROOT/directory).rglob("*.py")}
 spec=importlib.util.spec_from_file_location("v22_original_parser",BASE/"before/src/services/pdf_parser.py")
 before=importlib.util.module_from_spec(spec);spec.loader.exec_module(before)
 docs=json.loads((ROOT/"outputs/reading_recovery_20261015/known_unresolved_cases.json").read_text())
 sample=json.loads((ROOT/"outputs/putuo_visual_compare_10_20261014/selection.json").read_text())["documents"]
 bysha={d["sha256"]:d for d in docs if d["reading_gaps"]}
 for d in sample:bysha.setdefault(d["sha256"],d)
 cases={"就业促进": [(8,"open_fiscal_tail"),(11,"open_basic_tail")],"眼病牙病":[(12,"missing_money_grid")],"精神卫生":[(5,"decimal_wrap")],"机关事务管理局2025年度单位":[(9,"merged_rows")]}
 results=[]
 for d in bysha.values():
  path=Path(d["source_pdf"]);assert sha(path)==d["sha256"]
  old=before.PDFParser(None).build_parse_artifact(str(path));a=PDFParser(None).build_parse_artifact(str(path))
  assert not (values(old)-values(a)),(path.name,"previous native numeric cells changed")
  assert ParseArtifact.from_dict(a.to_dict()).to_dict()==a.to_dict()
  texts=["\n".join(b.text for b in a.text_blocks if b.page_number==page) for page in range(1,a.page_count+1)]
  profile=resolve_document_profile(filename=path.name,page_texts=texts)
  context=build_fact_context_from_artifact(a,org_name=profile.organization_name.value,fiscal_year=profile.report_year.value,report_kind=profile.report_kind.value)
  facts=context.facts if context else []
  expected=[(page,kind) for part,items in cases.items() if part in path.name for page,kind in items]
  matches=[]
  with pymupdf.open(path) as pdf:
   for page,kind in expected:
    for code,name,indicator,amount in EXPECTED[kind]:
     candidates=[f for f in facts if f.subject_code==code and key(f.subject_name)==name and f.indicator==indicator
                 and any(r["physical_page"]==page for r in f.extra_dims["evidence_refs"])]
     assert len(candidates)==1,(path.name,code,name,indicator,len(candidates))
     f=candidates[0];assert f.raw_value==amount and operand_rejection_reason(f,profile.organization_name.value) is None
     ref=f.extra_dims["evidence_refs"][0];box=ref["bbox"]
     literal=pdf[page-1].get_textbox(pymupdf.Rect(box)+(-.5,-2,.5,2))
     assert key(literal)==amount,(path.name,page,amount,repr(literal),box)
     matches.append(dict(physical_page=page,subject_code=code,subject_name=name,indicator=indicator,
                         expected_amount=amount,actual_amount=f.raw_value,independent_source_text=literal,bbox=box,
                         source_cell_id=ref["source_cell_id"],evidence=ref))
  reading=assess_table_reading(a);assert not reading["gaps"],(path.name,reading["gaps"])
  out=BASE/"originals"/d["sha256"];out.mkdir(parents=True,exist_ok=True)
  (out/"artifact.json").write_text(json.dumps(a.to_dict(),ensure_ascii=False))
  (out/"facts.json").write_text(json.dumps([f.to_dict() for f in facts],ensure_ascii=False,indent=2))
  (out/"comparison.json").write_text(json.dumps(matches,ensure_ascii=False,indent=2))
  result=dict(filename=path.name,source_pdf=str(path),sha256=d["sha256"],source_unchanged=sha(path)==d["sha256"],
              previous_numeric_cells_preserved=sum(values(old).values()),repaired_money_cells=len(matches),
              reading_gaps=len(reading["gaps"]),unbound_money_cells=build_table_import_receipt(a,facts)["unresolved_money_cell_count"])
  results.append(result);print(path.name,result["repaired_money_cells"],"source and binding matched",flush=True)
 summary=dict(parser=PARSER_FINGERPRINT,documents=len(results),repaired_money_cells=sum(d["repaired_money_cells"] for d in results),
              previous_numeric_cells_preserved=sum(d["previous_numeric_cells_preserved"] for d in results),
              source_unchanged=all(d["source_unchanged"] for d in results),code_unchanged=all(sha(ROOT/p)==h for p,h in files.items()),
              independent_unseen_acceptance=False,results=results,code_fingerprints=files)
 assert summary["repaired_money_cells"]==19 and summary["source_unchanged"] and summary["code_unchanged"]
 (BASE/"original_verification.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in summary.items() if k not in {"results","code_fingerprints"}},ensure_ascii=False))


if __name__=="__main__":main()
