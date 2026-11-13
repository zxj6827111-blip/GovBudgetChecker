"""以冻结来源证据回归公开解析、财政事实与规则入口；输出不覆盖验收基线。"""
import argparse
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

import pdfplumber
BASE=Path(__file__).resolve().parent;ROOT=BASE.parents[1];sys.path.insert(0,str(ROOT))
from src.services.pdf_parser import PDFParser
from src.services.document_profile_resolver import resolve_document_profile
from src.engine.fact_context import build_fact_context_from_artifact,operand_rejection_reason
from src.services.table_import_receipt import build_table_import_receipt
from src.engine.pipeline import build_document,run_rules_with_outcomes
from src.services.structure_view import build_structure_view


def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str)+'\n')
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def codes():return {str(p.relative_to(ROOT)):sha(p) for n in ('src','api') for p in (ROOT/n).rglob('*.py')}


def main():
 ap=argparse.ArgumentParser();ap.add_argument('phase');ap.add_argument('--batch',choices=['new9','new10','old15'],default='new9');ap.add_argument('--docs',nargs='*');ap.add_argument('--no-rules',action='store_true');args=ap.parse_args()
 frozen=ROOT/'outputs'/dict(new9='shanghai_2024_new9_acceptance_20261016',new10='shanghai_2024_new10_acceptance_20261015',old15='shanghai_2024_v23_acceptance_20261015')[args.batch]
 comparator=(frozen/'compare_source.py' if args.batch=='new10' else ROOT/'outputs/native_fiscal_coverage_repair_20261015_v24/compare_source_v24.py')
 dest=BASE/args.phase;dest.mkdir(exist_ok=False)
 inventory=json.loads((frozen/'inventory.json').read_text())
 if args.docs:inventory=[d for d in inventory if d['doc_id'] in args.docs]
 write(dest/'inventory.json',inventory);write(dest/'code_before.json',codes())
 for name in ['adjudications.json','source_truth_overlay.json','identity_adjudication']:
  if (frozen/name).exists():(dest/name).symlink_to(frozen/name)
 if args.batch=='old15' and not (BASE/'identity_adjudication').exists():(BASE/'identity_adjudication').symlink_to(ROOT/'outputs/native_fiscal_coverage_repair_20261015_v24/identity_adjudication')
 results=[]
 for entry in inventory:
  out=dest/'documents'/entry['doc_id'];out.mkdir(parents=True)
  for name in ['source.pdf','source_oracle.json']:(out/name).symlink_to(frozen/'documents'/entry['doc_id']/name)
  assert sha(out/'source.pdf')==entry['sha256'];start=time.monotonic()
  artifact=PDFParser(None).build_parse_artifact(str(out/'source.pdf'))
  with pdfplumber.open(out/'source.pdf') as pdf:texts=[p.extract_text() or '' for p in pdf.pages]
  profile=resolve_document_profile(filename=entry['filename'],page_texts=texts)
  org=profile.organization_name.value
  ctx=build_fact_context_from_artifact(artifact,org_name=org,fiscal_year=profile.year,report_kind=profile.kind)
  facts=ctx.facts if ctx else []
  write(out/'artifact.json',artifact.to_dict());write(out/'facts.json',[f.to_dict() for f in facts])
  write(out/'receipt.json',build_table_import_receipt(artifact,facts,org_name=org))
  write(out/'profile.json',dict(organization=org,year=profile.year,kind=profile.kind,organization_basis=str(profile.organization_name)))
  write(out/'fact_admission.json',[{'fact_uid':f.fact_uid,'reason':operand_rejection_reason(f,org)} for f in facts])
  issue_rows=[]
  if not args.no_rules:
   tables=[[] for _ in texts]
   for t in artifact.physical_fragments:
    if t.extraction_method!='page_text_fallback':tables[t.page_number-1].append(t.rows)
   doc=build_document(entry['filename'],texts,tables,(out/'source.pdf').stat().st_size)
   doc.parse_artifact=artifact;doc.fact_context=ctx;doc.document_profile=profile;doc.structure_view=build_structure_view(texts,artifact)
   issues,outcomes=run_rules_with_outcomes(doc,report_kind=profile.kind)
   issue_rows=[asdict(i) for i in issues];write(out/'engine_issues.json',issue_rows)
   write(out/'rule_outcomes.json',[asdict(o) for o in outcomes])
   write(out/'check_receipts.json',getattr(doc,'fact_receipts',[]))
  result=dict(doc_id=entry['doc_id'],facts=len(facts),issues=len(issue_rows),parser=artifact.parser_fingerprint,elapsed=round(time.monotonic()-start,2))
  results.append(result);write(dest/'parser_results.json',results);print(result,flush=True)
 write(dest/'code_after_parse.json',codes())
 if args.batch=='old15':
  spec=importlib.util.spec_from_file_location('source_comparator',comparator);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);m.BASE=dest;m.main()


if __name__=='__main__':main()
