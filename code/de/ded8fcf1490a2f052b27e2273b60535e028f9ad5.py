"""新9份实际数据库完整回读；技术一致与来源识别结果分别报告。"""
from collections import Counter
import importlib.util
import json
from pathlib import Path
import sys

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
sys.path.insert(0,str(ROOT))
from src.schemas.parse_artifact import FiscalFact
from src.engine.fact_context import operand_rejection_reason


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str)+'\n')


def obj(value):
    return json.loads(value) if isinstance(value,str) else value


receipt = read(BASE / 'service/service_receipt.json')
assert receipt['status']=='passed' and receipt['cleanup']=='complete'
inventory = read(BASE / 'inventory.json')
phase = BASE / 'database_public'
phase.mkdir(exist_ok=True)
write(phase / 'inventory.json',inventory)
results = []
metadata_differences=[]
for doc in inventory:
    public = BASE / 'documents' / doc['doc_id']
    db = BASE / 'service_database' / doc['doc_id']
    local,stored = read(public / 'facts.json'),read(db / 'facts.json')
    evidence = {e['evidence_id']:e for e in read(db / 'evidence.json')}
    for fact in stored:
        fact['extra_dims']=obj(fact['extra_dims'])
    actual = {f['fact_uid']:f for f in stored}
    assert len(actual)==len(stored)==len(local),doc['doc_id']
    fields=['raw_value','subject_code','subject_name','indicator','budget_stage','table_code','unit','fiscal_year','org_name']
    for fact in local:
        saved=actual[fact['fact_uid']]
        for key in fields:
            if saved[key]!=fact[key]:
                metadata_differences.append(dict(doc_id=doc['doc_id'],fact_uid=fact['fact_uid'],field=key,
                                                public=fact[key],stored=saved[key]))
        assert saved['extra_dims']['evidence_refs']==fact['extra_dims']['evidence_refs']
        for ref in saved['extra_dims']['evidence_refs']:
            persisted=evidence[ref['evidence_id']]
            assert all(persisted[k]==ref[k] for k in ['quote','bbox','file_hash','physical_page','source_cell_id'])
            assert obj(persisted['source_provenance'])==ref.get('source_provenance',{})
    artifact=read(db / 'artifact.json')
    public_artifact=read(public / 'artifact.json')
    assert artifact['source_file_hash']==doc['sha256']
    assert artifact['parser_fingerprint']==public_artifact['parser_fingerprint']
    assert artifact['source_grids']==public_artifact['source_grids']
    target=phase / 'documents' / doc['doc_id']
    target.mkdir(parents=True,exist_ok=True)
    for name in ['source.pdf','source_oracle.json']:
        if not (target/name).exists():(target/name).symlink_to((public/name).resolve())
    write(target/'artifact.json',artifact)
    write(target/'facts.json',stored)
    write(target/'fact_admission.json',[dict(fact_uid=f['fact_uid'],reason=operand_rejection_reason(FiscalFact.from_dict(f),f['org_name'])) for f in stored])
    differences=[row for row in metadata_differences if row['doc_id']==doc['doc_id']]
    results.append(dict(doc_id=doc['doc_id'],facts=len(stored),evidence=len(evidence),field_differences=len(differences),
                        evidence_parity='passed',source_grid_parity='passed'))
    print(doc['doc_id'],len(stored),'field differences',len(differences),'evidence parity passed',flush=True)
spec=importlib.util.spec_from_file_location('native_comparison',BASE/'compare_native_identity.py')
comparator=importlib.util.module_from_spec(spec)
spec.loader.exec_module(comparator)
rows,totals=comparator.compare_phase(phase,verify_pdf_characters=True)
source_pass=all(row['reading_status']=='read_exact' and row['fact_status']=='bound_exact' and not row['fact_admission_rejection'] for row in rows)
report=dict(service_storage_parity='metadata_difference' if metadata_differences else 'passed',recognition_acceptance='passed' if source_pass else 'not_passed',
            service_scenarios=len(receipt['scenarios']),original_documents=len(results),stored_facts=sum(r['facts'] for r in results),
            source_comparison=totals,cleanup=receipt['cleanup'],results=results,
            field_difference_count=len(metadata_differences),field_difference_types=dict(Counter(row['field'] for row in metadata_differences)),
            field_differences=metadata_differences)
write(BASE/'service_readback_verification.json',report)
print(json.dumps({k:v for k,v in report.items() if k not in ['results','field_differences']},ensure_ascii=False))
