"""只读原 PDF 的科目表文核验；隔离比较指定代码版本，不连接数据库或 AI。"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--code-root', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--fresh-jobs', nargs='*', default=[])
    args = parser.parse_args()
    sys.path.insert(0, str(args.code_root.resolve()))
    import pdfplumber
    from src.engine.fact_context import build_fact_context_from_artifact
    from src.engine.rule_outcome import RuleDeferred
    from src.engine.rules_v33 import build_document, R33222_Narrative5_T5, R33227_Narrative5_T5_NameConsistency
    from src.schemas.parse_artifact import ParseArtifact
    from src.services.input_processing_identity import bind_artifact_document_identity
    from src.services.pdf_parser import PDFParser

    args.cache.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rows = json.loads(args.manifest.read_text())
    results = []
    # 两版本共用缓存时，必须证明解析器源码一致；新鲜重解析可通过 --fresh-jobs 指定。
    parser_hash = hashlib.sha256((args.code_root / 'src/services/pdf_parser.py').read_bytes()).hexdigest()
    for row in rows:
        start = time.monotonic()
        path = Path(row['pdf'])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row['sha256'], path
        cache = args.cache / (row['sha256'] + '.json.gz')
        cached = cache.exists() and row['job'] not in args.fresh_jobs
        if cached:
            saved = json.loads(gzip.decompress(cache.read_bytes()))
            assert saved['parser_hash'] == parser_hash
            artifact = ParseArtifact.from_dict(saved['artifact'])
            texts = saved['texts']
        else:
            artifact = PDFParser(None).build_parse_artifact(str(path))
            with pdfplumber.open(path) as pdf:
                texts = [page.extract_text() or '' for page in pdf.pages]
            cache.write_bytes(gzip.compress(json.dumps(dict(parser_hash=parser_hash,
                              artifact=artifact.to_dict(), texts=texts), ensure_ascii=False).encode()))
        owner, year = row['organization'], row['year']
        bind_artifact_document_identity(artifact, organization_id=None, organization_name=owner,
                                       fiscal_year=year, report_kind='final', doc_type=row['doc_type'],
                                       check_subject_name=owner)
        tables = [[] for _ in texts]
        for table in artifact.physical_fragments:
            tables[table.page_number - 1].append(table.rows)
        doc = build_document(str(path), texts, tables, path.stat().st_size)
        doc.parse_artifact = artifact
        doc.fact_context = build_fact_context_from_artifact(artifact, org_name=owner, fiscal_year=year,
                                                           report_kind='final', doc_type=row['doc_type'])
        before = doc.fact_context.to_dict()
        rules = []
        for rule in (R33222_Narrative5_T5(), R33227_Narrative5_T5_NameConsistency()):
            try:
                issues, detail = rule.apply(doc), None
            except RuleDeferred as deferred:
                issues, detail = deferred.partial_issues, deferred.detail
            rules.append(dict(rule=rule.code, incomplete=detail,
                issues=[dict(message=i.message, severity=i.severity, location=i.location,
                             fact_ids=list(i.fact_ids or [])) for i in issues]))
        assert before == doc.fact_context.to_dict(), '核对不得修改原始事实快照'
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row['sha256'], '原PDF发生变化'
        receipts = getattr(doc, 'fact_receipts', [])
        result = dict(**row, parser_hash=parser_hash, used_cache=cached,
                      seconds=round(time.monotonic() - start, 2), original_and_facts_unchanged=True,
                      rules=rules, receipts=receipts)
        results.append(result)
        args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str))
        print(f"{len(results)}/{len(rows)} {row['job']} {row['filename']} "
              f"incomplete={sum(bool(r['incomplete']) for r in rules)} {result['seconds']}s", flush=True)


if __name__ == '__main__':
    main()
