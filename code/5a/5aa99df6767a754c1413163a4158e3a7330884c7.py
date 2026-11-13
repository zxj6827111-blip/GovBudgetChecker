"""从真实数据库回读的事实/证据逐格对照独立原页真值，含缓存后原始版本。"""
from collections import Counter
import json
import unicodedata
from pathlib import Path

BASE=Path(__file__).resolve().parent


def obj(value):return json.loads(value) if isinstance(value,str) else value

def compact(value):return ''.join(unicodedata.normalize('NFKC', str(value or '')).split())

def contains(box,small):
    x,y=(small[0]+small[2])/2,(small[1]+small[3])/2
    return box and box[0]-.8<=x<=box[2]+.8 and box[1]-.8<=y<=box[3]+.8


def main():
    receipt=json.loads((BASE/'service_complete/service_receipt.json').read_text())
    assert receipt['status']=='passed' and receipt['cleanup']=='complete' and receipt['source_unchanged_during_run']
    inventory=json.loads((BASE/'baseline/inventory.json').read_text());byhash={d['sha256']:d for d in inventory}
    observations=[];unique={};repaired=set()
    for scenario in receipt['scenarios']:
        doc=byhash.get(scenario['source_sha256'])
        if not doc:continue
        targets=json.loads((BASE/'final_complete/documents'/doc['doc_id']/'source_amount_truth.json').read_text())
        old=json.loads((BASE/'baseline/documents'/doc['doc_id']/'comparison.json').read_text())
        affected={r['truth_id'] for r in old if r['fact_status'] in {'missing','semantic_mismatch'}}
        for folder in sorted((BASE/'service_database'/scenario['job_id']).iterdir()):
            key=(scenario['job_id'],folder.name)
            if key in unique:continue
            a=json.loads((folder/'artifact.json').read_text());facts=json.loads((folder/'facts.json').read_text())
            evidence={e['evidence_id']:e for e in json.loads((folder/'evidence.json').read_text())}
            ingest=json.loads((folder/'ingest.json').read_text())
            assert a['parser_fingerprint']=='pdfplumber-cells-v24-continuation-and-fiscal-coverage'
            physical={f"{t['fragment_uid']}:r{c['row_idx']}c{c['col_idx']}":c for t in a['physical_fragments'] for c in t['cells']}
            refs=[(f,r) for f in facts for r in obj(f['extra_dims']).get('evidence_refs',[])]
            for idx,target in enumerate(targets):
                matched=[(f,r) for f,r in refs if r['physical_page']==target['page']
                         and contains(r.get('bbox'),target['bbox']) and f['raw_value']==target['raw_text']]
                assert len(matched)==1,(doc['doc_id'],idx,matched)
                fact,ref=matched[0];identity=target['expected']
                for field in ['subject_code','subject_name','indicator','budget_stage']:
                    if identity.get(field) is not None:assert compact(fact[field])==compact(identity[field]),(doc['doc_id'],idx,field)
                source=physical[ref['source_cell_id']]
                assert source['raw_text']==ref['quote']==fact['raw_value']
                assert source['bbox']==ref['bbox'] and ref['file_hash']==doc['sha256']
                stored=evidence[ref['evidence_id']]
                assert stored['quote']==ref['quote'] and stored['bbox']==ref['bbox']
                assert obj(stored['source_provenance'])==ref['source_provenance']
                truth_id=f"{doc['doc_id']}:{idx:05}"
                if truth_id in affected:
                    assert ref['source_provenance']['native_text_sources']
                    repaired.add(truth_id)
            unique[key]=len(targets)
            observations.append(dict(doc_id=doc['doc_id'],job_id=scenario['job_id'],analysis_revision=folder.name,
               original_source_cells_verified=len(targets),parse_run_id=ingest['parse_run_id'],parse_cache=ingest.get('parse_cache')))
    correction=next(s for s in receipt['scenarios'] if s['scenario']=='native_correction')
    assert correction['correction_validation_passed']
    assert correction['correction']['cache_readback_validation_passed'] and correction['correction']['original_parse_basis_preserved']
    cache=json.loads((BASE/'service_cache_isolation.json').read_text())
    assert len(cache)==3 and all(r['legacy_created_after_current'] and r['v24_lookup_rejected_newer_v23'] for r in cache)
    assert len(repaired)==102
    result=dict(status='passed',service_scenarios=len(receipt['scenarios']),unique_original_documents=3,
                unique_repaired_cells_verified=len(repaired),source_cell_observations_verified=sum(unique.values()),
                cache_reanalysis_and_original_history_preserved=True,newer_stale_v23_cache_rejected=True,
                database_snapshots=observations,cleanup=receipt['cleanup'])
    (BASE/'service_readback_verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(result)


if __name__=='__main__':main()
