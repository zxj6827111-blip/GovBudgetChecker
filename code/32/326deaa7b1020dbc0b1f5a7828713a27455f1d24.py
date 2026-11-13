"""固定材料回归：冻结真值只读，公开解析/财政事实入口输出另存。"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
FROZEN = ROOT / 'outputs/shanghai_2024_v23_acceptance_20261015'
sys.path.insert(0, str(ROOT))
from src.services.pdf_parser import PDFParser
from src.services.document_profile_resolver import resolve_document_profile
from src.engine.fact_context import build_fact_context_from_artifact, operand_rejection_reason
from src.services.table_import_receipt import build_table_import_receipt


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str)+'\n')


def code_hashes():
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in ('src', 'api') for p in (ROOT/folder).rglob('*.py')}


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('phase');ap.add_argument('--docs',nargs='*');args=ap.parse_args()
    out=BASE/args.phase;out.mkdir(exist_ok=True)
    inventory=json.loads((FROZEN/'inventory.json').read_text())
    if args.docs:inventory=[d for d in inventory if d['doc_id'] in args.docs]
    write(out/'inventory.json',inventory)
    write(out/'adjudications.json',json.loads((FROZEN/'adjudications.json').read_text()))
    write(out/'code_before.json',code_hashes())
    results=[]
    for row in inventory:
        dest=out/'documents'/row['doc_id'];dest.mkdir(parents=True,exist_ok=True)
        for name in ('source.pdf','source_oracle.json'):
            target=dest/name
            if not target.exists():target.symlink_to(FROZEN/'documents'/row['doc_id']/name)
        assert hashlib.sha256((dest/'source.pdf').read_bytes()).hexdigest()==row['sha256']
        started=time.monotonic()
        artifact=PDFParser(None).build_parse_artifact(str(dest/'source.pdf'))
        profile=resolve_document_profile(filename=row['filename'],page_texts=[
            '\n'.join(b.text for b in artifact.text_blocks if b.page_number==p) for p in range(1,artifact.page_count+1)])
        org=profile.organization_name.value
        ctx=build_fact_context_from_artifact(artifact,org_name=org,fiscal_year=profile.report_year.value,report_kind=profile.report_kind.value)
        facts=ctx.facts if ctx else []
        write(dest/'artifact.json',artifact.to_dict());write(dest/'facts.json',[f.to_dict() for f in facts])
        write(dest/'receipt.json',build_table_import_receipt(artifact,facts,org_name=org))
        write(dest/'fact_admission.json',[{'fact_uid':f.fact_uid,'reason':operand_rejection_reason(f,org)} for f in facts])
        result=dict(doc_id=row['doc_id'],facts=len(facts),parser=artifact.parser_fingerprint,elapsed=round(time.monotonic()-started,2))
        results.append(result);write(out/'parser_results.json',results);print(result,flush=True)
    write(out/'code_after_parse.json',code_hashes())
    spec=importlib.util.spec_from_file_location('frozen_comparator',BASE/'compare_source_v24.py' if args.phase.startswith('final') else FROZEN/'compare_source.py')
    comparator=importlib.util.module_from_spec(spec);spec.loader.exec_module(comparator)
    comparator.BASE=out;comparator.main()


if __name__=='__main__':main()
