"""按隔离服务回执的原件SHA选9份stage1结果，明确排除预算控制/人工更正案例。"""
import argparse
import hashlib
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
FROZEN = ROOT / 'outputs/shanghai_2024_new9_acceptance_20261016'


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(service_directory, name):
    receipt_path = service_directory / 'service_receipt.json'
    receipt = read(receipt_path)
    assert receipt['status'] == 'passed', receipt.get('status')
    by_hash = {doc['sha256']: doc['doc_id'] for doc in read(FROZEN / 'inventory.json')}
    reports, evidence, excluded = {}, [], []
    for row in receipt['scenarios']:
        scenario = row['scenario']
        if scenario in ['native_budget', 'native_correction']:
            excluded.append(dict(scenario=scenario, source_sha256=row['source_sha256'], reason='合成预算控制或人工更正，不属于9原件初始stage1结果'))
            continue
        doc_id = by_hash.get(row['source_sha256'])
        assert doc_id is not None and doc_id not in reports, (scenario, doc_id)
        path = (service_directory / scenario / 'report.json').resolve()
        assert sha(path) == row['exports']['report.json'], scenario
        source = path.parent / 'source.pdf'
        assert sha(source) == row['source_sha256'], scenario
        report = read(path)
        assert report['job_id'] == row['job_id'], scenario
        reports[doc_id] = str(path)
        evidence.append(dict(doc_id=doc_id, scenario=scenario, source_sha256=row['source_sha256'],
                             report=str(path), report_sha256=sha(path), job_id=row['job_id'],
                             original_stage1_export_matches_service_receipt=True))
    assert len(reports) == len(by_hash) == 9
    output = BASE / name
    output.write_text(json.dumps(reports, ensure_ascii=False, indent=2) + '\n')
    output.with_name(output.stem + '_proof.json').write_text(json.dumps(dict(source_receipt=str(receipt_path), receipt_sha256=sha(receipt_path),
        original_stage1_documents=9, reads_corrected_exports=False, records=evidence, excluded_scenarios=excluded), ensure_ascii=False, indent=2) + '\n')
    print(output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('service_directory')
    parser.add_argument('--output', default='api_report_map_verified.json')
    args = parser.parse_args()
    main(Path(args.service_directory).resolve(), args.output)
