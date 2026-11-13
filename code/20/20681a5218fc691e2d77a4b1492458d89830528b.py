"""保留旧19格及原先正确的金额/科目/物理身份；以旧冻结产物为比较基线。"""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

import pymupdf
BASE=Path(__file__).resolve().parent;ROOT=BASE.parents[1];sys.path.insert(0,str(ROOT))
from src.services.pdf_parser import PDFParser
from src.services.document_profile_resolver import resolve_document_profile
from src.engine.fact_context import build_fact_context_from_artifact,operand_rejection_reason

OLD=ROOT/'outputs/native_structure_repair_20261015'


def compact(s):return re.sub(r'\s+','',str(s or ''))

def cells(artifact):
    return Counter((c['page_number'],c['row_idx'],c['col_idx'],tuple(c['bbox'] or []),c['raw_text'],c['numeric_value'])
                   for t in artifact['physical_fragments'] for c in t['cells'] if c['numeric_value'] is not None)


def main():
    original=json.loads((OLD/'original_verification.json').read_text())
    results=[]; verified=0
    for document in original['results']:
        path=Path(document['source_pdf']);sha=hashlib.sha256(path.read_bytes()).hexdigest();assert sha==document['sha256']
        oldfolder=OLD/'originals'/sha
        old=json.loads((oldfolder/'artifact.json').read_text())
        artifact=PDFParser(None).build_parse_artifact(str(path));new=artifact.to_dict()
        assert not (cells(old)-cells(new)),path.name
        profile=resolve_document_profile(filename=path.name,page_texts=['\n'.join(b.text for b in artifact.text_blocks if b.page_number==page) for page in range(1,artifact.page_count+1)])
        ctx=build_fact_context_from_artifact(artifact,org_name=profile.organization_name.value,fiscal_year=profile.report_year.value,report_kind=profile.report_kind.value)
        byuid={f.fact_uid:f for f in ctx.facts}
        oldfacts=json.loads((oldfolder/'facts.json').read_text())
        for previous in oldfacts:
            actual=byuid[previous['fact_uid']]
            for field in ('raw_value','subject_code','subject_name','indicator','budget_stage','table_code'):
                assert getattr(actual,field)==previous[field],(path.name,field,previous,actual)
            for ref in previous['extra_dims']['evidence_refs']:
                current=actual.extra_dims['evidence_refs'][0]
                for field in ('source_cell_id','physical_page','file_hash','bbox','quote'):
                    assert ref[field]==current[field],(path.name,field)
        targets=json.loads((oldfolder/'comparison.json').read_text())
        with pymupdf.open(path) as pdf:
            for target in targets:
                facts=[f for f in ctx.facts if f.subject_code==target['subject_code'] and compact(f.subject_name)==target['subject_name']
                       and f.indicator==target['indicator'] and any(r['physical_page']==target['physical_page'] for r in f.extra_dims['evidence_refs'])]
                assert len(facts)==1
                fact=facts[0];assert fact.raw_value==target['expected_amount']
                assert operand_rejection_reason(fact,profile.organization_name.value) is None
                ref=fact.extra_dims['evidence_refs'][0]
                assert compact(pdf[target['physical_page']-1].get_textbox(pymupdf.Rect(ref['bbox'])+(-.5,-2,.5,2)))==fact.raw_value
        verified+=len(targets)
        dest=BASE/'known19'/sha;dest.mkdir(parents=True,exist_ok=True)
        (dest/'artifact.json').write_text(json.dumps(new,ensure_ascii=False))
        (dest/'facts.json').write_text(json.dumps([f.to_dict() for f in ctx.facts],ensure_ascii=False))
        results.append(dict(filename=path.name,previous_numeric_cells_preserved=sum(cells(old).values()),previous_facts_preserved=len(oldfacts),verified=len(targets),source_sha256=sha))
        print(path.name,len(targets),'passed',flush=True)
    assert verified==19
    result=dict(documents=len(results),verified=verified,previous_numeric_cells_preserved=sum(r['previous_numeric_cells_preserved'] for r in results),previous_facts_and_evidence_preserved=sum(r['previous_facts_preserved'] for r in results),results=results,
                prior_three_unlabelled_source_evidence_untouched=True)
    (BASE/'known_19_reverification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
