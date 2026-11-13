"""比对新批真实数据库、公开入口及独立原页金额；允许记录已发现的缺陷。"""
from collections import Counter, defaultdict
import json
from pathlib import Path
import unicodedata

BASE=Path(__file__).resolve().parent


def obj(value):
    return json.loads(value) if isinstance(value,str) else value


def compact(value):
    return ''.join(unicodedata.normalize('NFKC',str(value or '')).split())


def contains(box,small):
    if not box or not small:
        return False
    x,y=(small[0]+small[2])/2,(small[1]+small[3])/2
    return box[0]-.8<=x<=box[2]+.8 and box[1]-.8<=y<=box[3]+.8


def main():
    receipt=json.loads((BASE/'service/service_receipt.json').read_text())
    assert receipt['status']=='passed' and receipt['cleanup']=='complete'
    results=[]
    for doc in json.loads((BASE/'inventory.json').read_text()):
        folder=BASE/'documents'/doc['doc_id'];db=BASE/'service_database'/doc['doc_id']
        local=json.loads((folder/'facts.json').read_text())
        stored=json.loads((db/'facts.json').read_text())
        artifact=json.loads((db/'artifact.json').read_text())
        assert artifact['source_file_hash']==doc['sha256']
        assert artifact['parser_fingerprint']=='pdfplumber-cells-v24-continuation-and-fiscal-coverage'
        byuid={f['fact_uid']:f for f in stored}
        assert len(byuid)==len(stored)==len(local)
        evidence={e['evidence_id']:e for e in json.loads((db/'evidence.json').read_text())}
        parity=[]
        for fact in local:
            actual=byuid[fact['fact_uid']]
            for key in ['raw_value','subject_code','subject_name','indicator','budget_stage','table_code','unit','fiscal_year']:
                if actual[key]!=fact[key]:parity.append(dict(fact_uid=fact['fact_uid'],field=key,public=fact[key],stored=actual[key]))
            original_refs=fact['extra_dims']['evidence_refs']
            stored_refs=obj(actual['extra_dims'])['evidence_refs']
            assert original_refs==stored_refs
            for ref in stored_refs:
                saved=evidence[ref['evidence_id']]
                assert saved['quote']==ref['quote'] and saved['bbox']==ref['bbox']
                assert obj(saved['source_provenance'])==ref['source_provenance']
        refs=defaultdict(list)
        for fact in stored:
            for ref in obj(fact['extra_dims'])['evidence_refs']:refs[ref['physical_page']].append((fact,ref))
        comparisons=[]
        for idx,source in enumerate(json.loads((folder/'source_amount_truth.json').read_text())):
            possible=[(f,r) for f,r in refs[source['page']] if contains(r['bbox'],source['bbox'])]
            exact=[(f,r) for f,r in possible if compact(f['raw_value'])==compact(source['raw_text'])]
            status='bound_exact' if len(exact)==1 else ('wrong_text' if possible else 'missing')
            if len(exact)==1:
                fact,ref=exact[0]
                expected=source['expected']
                if any(expected.get(k) is not None and compact(expected[k])!=compact(fact[k]) for k in ['subject_code','subject_name','indicator','budget_stage']):
                    status='semantic_mismatch'
            comparisons.append(dict(truth_id=f"{doc['doc_id']}:{idx:05}",status=status,amount=source['raw_text'],page=source['page']))
        (db/'source_comparison.json').write_text(json.dumps(comparisons,ensure_ascii=False,indent=2))
        result=dict(doc_id=doc['doc_id'],filename=doc['filename'],facts=len(stored),public_database_parity_errors=parity,
                    source_comparison=dict(Counter(r['status'] for r in comparisons)))
        results.append(result)
        print(doc['doc_id'],len(stored),'parity_errors',len(parity),result['source_comparison'])
    total=Counter()
    for row in results:total.update(row['source_comparison'])
    summary=dict(status='service_storage_parity_passed' if not any(r['public_database_parity_errors'] for r in results) else 'parity_difference',
                 service_scenarios=len(receipt['scenarios']),unique_original_documents=len(results),stored_facts=sum(r['facts'] for r in results),
                 source_amount_cells=sum(total.values()),source_comparison=dict(total),cleanup=receipt['cleanup'],
                 recognition_acceptance='not_passed',results=results)
    (BASE/'service_readback_verification.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in summary.items() if k!='results'},ensure_ascii=False))


if __name__=='__main__':
    main()
