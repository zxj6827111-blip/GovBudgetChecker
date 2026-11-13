"""对齐v25实际公开入口的完整原页文本；保留旧验收脚本的profile差异。"""
import json
from pathlib import Path
import shutil
import sys

import pdfplumber

BASE=Path(__file__).resolve().parent
ROOT=BASE.parents[1]
sys.path.insert(0,str(ROOT))
from src.schemas.parse_artifact import ParseArtifact
from src.services.document_profile_resolver import resolve_document_profile
from src.engine.fact_context import build_fact_context_from_artifact,operand_rejection_reason
from src.services.table_import_receipt import build_table_import_receipt

records=[]
for doc in json.loads((BASE/'inventory.json').read_text()):
    folder=BASE/'documents'/doc['doc_id']
    snapshot=BASE/'initial_artifact_text_profile'/doc['doc_id']
    snapshot.mkdir(parents=True,exist_ok=False)
    for name in ['facts.json','receipt.json','fact_admission.json']:
        shutil.copy2(folder/name,snapshot/name)
    original=json.loads((folder/'facts.json').read_text())
    artifact=ParseArtifact.from_dict(json.loads((folder/'artifact.json').read_text()))
    with pdfplumber.open(folder/'source.pdf') as pdf:
        texts=[page.extract_text() or '' for page in pdf.pages]
    profile=resolve_document_profile(filename=doc['filename'],page_texts=texts)
    org=profile.organization_name.value
    assert org,doc['doc_id']
    context=build_fact_context_from_artifact(artifact,org_name=org,fiscal_year=profile.year,report_kind=profile.kind)
    previous={fact['fact_uid']:fact for fact in original}
    fields=['raw_value','subject_code','subject_name','indicator','budget_stage','table_code','unit','fiscal_year']
    for fact in context.facts:
        assert all(fact.to_dict()[key]==previous[fact.fact_uid][key] for key in fields)
        assert fact.extra_dims['evidence_refs']==previous[fact.fact_uid]['extra_dims']['evidence_refs']
    values=[fact.to_dict() for fact in context.facts]
    for name,data in [('facts',values),('receipt',build_table_import_receipt(artifact,context.facts,org_name=org)),
                      ('fact_admission',[dict(fact_uid=f.fact_uid,reason=operand_rejection_reason(f,org)) for f in context.facts])]:
        (folder/f'{name}.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    records.append(dict(doc_id=doc['doc_id'],previous_orgs=sorted({str(f.get('org_name')) for f in original}),
                        actual_public_page_text_org=org,unchanged_amount_identity_and_evidence=True,facts=len(values)))
shutil.copy2(BASE/'parser_results.json',BASE/'initial_artifact_text_profile/parser_results.json')
report=dict(kind='acceptance_harness_profile_basis_reconciliation',production_code_changed=False,
            basis='v25 run_batch.py与实际服务使用完整原页文本；初始旧脚本只拼接artifact文本块。',
            original_failure_log='verify_service_readback_initial_profile_basis.log',records=records)
(BASE/'profile_basis_reconciliation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False))
