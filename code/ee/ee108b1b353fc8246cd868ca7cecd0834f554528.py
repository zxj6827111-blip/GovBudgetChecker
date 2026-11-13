"""真实数据库回读与公开入口逐事实/证据一致，再按冻结原字符真值独立对照。"""
import importlib.util
import json
from pathlib import Path
import sys

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
sys.path.insert(0, str(ROOT))
from src.schemas.parse_artifact import FiscalFact
from src.engine.fact_context import operand_rejection_reason


def read(path):
    return json.loads(path.read_text())


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, default=str, indent=2) + '\n')


def obj(value):
    return json.loads(value) if isinstance(value, str) else value


def main():
    run = BASE / (sys.argv[1] if len(sys.argv) > 1 else 'service_verified')
    phase = BASE / (sys.argv[2] if len(sys.argv) > 2 else 'verified_new9')
    receipt = read(run / 'service_complete/service_receipt.json')
    assert receipt['cleanup'] == 'complete'
    assert receipt['status'] == 'passed'
    inventory = read(phase / 'inventory.json')
    dest = run / 'database_source_phase'
    dest.mkdir(exist_ok=True)
    write(dest / 'inventory.json', inventory)
    results = []
    fields = ['raw_value', 'subject_code', 'subject_name', 'indicator', 'budget_stage', 'table_code', 'unit', 'fiscal_year', 'org_name']
    for doc in inventory:
        folder = phase / 'documents' / doc['doc_id']
        snapshots = sorted((run / 'service_database' / doc['doc_id']).glob('*/*/facts.json'))
        assert snapshots, doc['doc_id']
        original = read(folder / 'facts.json')
        for snapshot in snapshots:
            db = snapshot.parent
            stored = read(snapshot)
            for row in stored:
                row['extra_dims'] = obj(row['extra_dims'])
            by_uid = {f['fact_uid']: f for f in stored}
            assert len(by_uid) == len(stored) == len(original), doc['doc_id']
            evidence = {e['evidence_id']: e for e in read(db / 'evidence.json')}
            for fact in original:
                actual = by_uid[fact['fact_uid']]
                assert all(actual[k] == fact[k] for k in fields), (doc['doc_id'], fact['fact_uid'])
                assert actual['extra_dims']['evidence_refs'] == fact['extra_dims']['evidence_refs']
                for ref in actual['extra_dims']['evidence_refs']:
                    saved = evidence[ref['evidence_id']]
                    for key in ('quote', 'bbox', 'file_hash', 'physical_page', 'source_cell_id'):
                        assert saved[key] == ref[key], (doc['doc_id'], key)
                    assert obj(saved['source_provenance']) == ref.get('source_provenance', {})
            artifact = read(db / 'artifact.json')
            public = read(folder / 'artifact.json')
            assert artifact['source_file_hash'] == doc['sha256']
            assert artifact['parser_fingerprint'] == public['parser_fingerprint']
            assert artifact['source_grids'] == public['source_grids']
        target = dest / 'documents' / doc['doc_id']
        target.mkdir(parents=True, exist_ok=True)
        for name in ('source.pdf', 'source_oracle.json'):
            if not (target / name).exists():
                (target / name).symlink_to((folder / name).resolve())
        # 写入DB实际回读数据；真值始终由独立比较器从冻结证据读取。
        write(target / 'artifact.json', artifact)
        scenario, = [s for s in receipt['scenarios'] if s['source_sha256'] == doc['sha256'] and s['scenario'] != 'native_correction']
        status = read(run / 'service_complete' / scenario['scenario'] / 'status.json')
        profile = status.get('document_profile') or status.get('result', {}).get('meta', {}).get('document_profile')
        assert profile and profile['organization_name']['value'] == stored[0]['org_name']
        write(target / 'profile.json', profile)
        write(target / 'facts.json', stored)
        write(target / 'fact_admission.json', [dict(fact_uid=f['fact_uid'], reason=operand_rejection_reason(FiscalFact.from_dict(f), f.get('org_name'))) for f in stored])
        results.append(dict(doc_id=doc['doc_id'], facts=len(stored), evidence=len(evidence), database_snapshots_checked=len(snapshots), parity_errors=0))
        print(doc['doc_id'], len(stored), 'DB facts/evidence parity passed', flush=True)
    spec = importlib.util.spec_from_file_location('native_identity', BASE / 'compare_source_v26.py')
    compare = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compare)
    summary = compare.compare(dest, verify_pdf_characters=True)
    assert summary['facts'] == {'bound_exact': 4235}
    assert summary['admitted_and_matched'] == 4235
    assert summary['source_owner_mismatches'] == summary['source_profile_owner_mismatches'] == 0
    assert summary['recognized_core_regions'] == summary['source_core_regions'] == 110
    cache = read(run / 'service_cache_isolation.json')
    assert {c['doc_id'] for c in cache} == {d['doc_id'] for d in inventory}
    assert all(c['legacy_created_after_current'] and c['v26_lookup_rejected_newer_v25'] for c in cache)
    correction, = [s for s in receipt['scenarios'] if s['scenario'] == 'native_correction']
    assert correction['correction_validation_passed']
    c = correction['correction']
    assert all(c['history_identical'].values()) and c['original_parse_basis_preserved']
    assert c['cache_readback_validation_passed'] and c['rule_status_changed']
    report = dict(status='passed', scenarios=len(receipt['scenarios']), original_pdfs=len(results), stored_facts=sum(r['facts'] for r in results),
                  source_comparison=summary, old_cache_versions_rejected=len(cache), history_identical=c['history_identical'],
                  corrected_overlay_preserved_original_parse=True, cleanup=receipt['cleanup'], results=results)
    write(run / 'readback_verification.json', report)
    print(json.dumps({k: v for k, v in report.items() if k != 'results'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
