"""冻结完成后执行当前公开解析入口，保留完整产物供原页反查。"""
import hashlib
import json
from pathlib import Path
import sys
import time

import pdfplumber

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
sys.path.insert(0, str(ROOT))
from src.services.pdf_parser import PDFParser, PARSER_FINGERPRINT
from src.services.document_profile_resolver import resolve_document_profile
from src.services.table_import_receipt import build_table_import_receipt
from src.engine.fact_context import build_fact_context_from_artifact, operand_rejection_reason


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write(p, v):
    p.write_text(json.dumps(v, ensure_ascii=False, indent=2) + '\n')


def main():
    seal = json.loads((BASE / 'source_freeze.json').read_text())
    inventory = json.loads((BASE / 'inventory.json').read_text())
    assert sha(BASE / 'inventory.json') == seal['inventory_sha256']
    code = json.loads((BASE / 'code_before.json').read_text())
    assert all(sha(ROOT / p) == h for p, h in code.items())
    results = []
    for row in inventory:
        out = BASE / 'documents' / row['doc_id']
        assert sha(out / 'source_oracle.json') == seal['oracles'][row['doc_id']]
        assert sha(out / 'source.pdf') == row['sha256']
        start = time.monotonic()
        try:
            artifact = PDFParser(None).build_parse_artifact(str(out / 'source.pdf'))
            with pdfplumber.open(out / 'source.pdf') as pdf:
                texts = [page.extract_text() or '' for page in pdf.pages]
            profile = resolve_document_profile(filename=row['filename'], page_texts=texts)
            org = profile.organization_name.value
            context = build_fact_context_from_artifact(artifact, org_name=org,
                       fiscal_year=profile.report_year.value, report_kind=profile.report_kind.value)
            facts = context.facts if context else []
            receipt = build_table_import_receipt(artifact, facts, org_name=org)
            write(out / 'artifact.json', artifact.to_dict())
            write(out / 'facts.json', [f.to_dict() for f in facts])
            write(out / 'receipt.json', receipt)
            write(out / 'fact_admission.json', [{'fact_uid': f.fact_uid, 'reason': operand_rejection_reason(f, org)} for f in facts])
            result = dict(doc_id=row['doc_id'], filename=row['filename'], pages=artifact.page_count,
                          parser=artifact.parser_fingerprint, organization=org, year=profile.report_year.value,
                          report_kind=profile.report_kind.value, physical_fragments=len(artifact.physical_fragments),
                          logical_tables=len(artifact.logical_tables), facts=len(facts),
                          admitted_facts=sum(operand_rejection_reason(f, org) is None for f in facts),
                          reading=receipt['reading_quality'],
                          elapsed_seconds=round(time.monotonic() - start, 2))
        except Exception as exc:
            result = dict(doc_id=row['doc_id'], filename=row['filename'], error=f'{type(exc).__name__}: {exc}')
        results.append(result)
        write(BASE / 'parser_results.json', results)
        print(row['doc_id'], row['filename'], {k: result.get(k) for k in ('facts', 'physical_fragments', 'admitted_facts', 'error', 'elapsed_seconds')}, flush=True)
    write(BASE / 'code_after_parse.json', {str(p.relative_to(ROOT)): sha(p) for name in ('src', 'api') for p in (ROOT / name).rglob('*.py')})
    print('FINISHED', PARSER_FINGERPRINT, flush=True)


if __name__ == '__main__':
    main()
