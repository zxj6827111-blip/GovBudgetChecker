"""只读原件重解析复现；不连接数据库、不调用OCR、不修改生产逻辑。"""
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from collections import Counter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ['TESTING'] = 'true'
os.environ['GOVBUDGET_AUTH_ENABLED'] = 'false'
os.environ.pop('DATABASE_URL', None)
import pdfplumber
import pymupdf as fitz
from src.services.pdf_parser import PDFParser
from src.services.document_profile_resolver import resolve_document_profile
from src.engine.fact_context import build_fact_context_from_artifact
from src.services.fiscal_column_identity import canonical_column_mappings, header_paths
from src.services.fiscal_table_rules import NINE_TABLE_RULES
from src.engine.pipeline import build_document
from src.engine.budget_rules import BUD003_YearConsistency

OUT = Path(__file__).resolve().parent
SOURCE = ROOT / 'outputs/pdf_acceptance_shanghai2022_20261021'
manifest = json.loads((SOURCE / 'source_manifest.json').read_text())
baseline = json.loads((SOURCE / 'baseline/code_fingerprints.json').read_text())
changed = [name for name, digest in baseline.items()
           if not (ROOT/name).is_file() or hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != digest]
assert not changed, changed
results = []
for doc_id, pages in [('SH2022-02',[17,27,33]), ('SH2022-15',[10,15]),
                      ('SH2022-20',[12]), ('SH2022-47',[1,2,3])]:
    source = next(s for s in manifest if s['doc_id'] == doc_id)
    path = Path(source['frozen_path'])
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest == source['sha256']
    started = time.monotonic()
    with pdfplumber.open(path) as pdf:
        texts = [p.extract_text() or '' for p in pdf.pages]
        artifact = PDFParser(None).build_parse_artifact_from_handle(pdf, file_hash=digest)
    profile = resolve_document_profile(filename=path.name, page_texts=texts)
    context = build_fact_context_from_artifact(artifact, org_name=profile.organization_name.value,
        fiscal_year=profile.year, report_kind=profile.kind, doc_type='dept_budget',
        require_complete_identity=True)
    target = OUT / doc_id
    target.mkdir(exist_ok=True)
    (target/'artifact.json').write_text(json.dumps(artifact.to_dict(),ensure_ascii=False))
    (target/'fact_context.json').write_text(json.dumps(context.to_dict(),ensure_ascii=False))
    tables = []
    for table in artifact.table_fragments:
        if table.page_number not in pages or table.table_code not in NINE_TABLE_RULES:
            continue
        mappings = canonical_column_mappings(table,NINE_TABLE_RULES[table.table_code])
        facts = [f for f in context.facts if any(r.get('physical_page')==table.page_number
                    for r in f.extra_dims.get('evidence_refs',[])) and f.table_code==table.table_code]
        tables.append({'page':table.page_number,'code':table.table_code,'title':table.title,
            'header_paths':header_paths(table),'mappings':mappings,'fact_count':len(facts),
            'first_facts':[{'indicator':f.indicator,'raw_value':f.raw_value,'subject_code':f.subject_code,
                'subject_name':f.subject_name,'org_name':f.org_name,'status':f.recognition_status}
                for f in facts[:9]],'literal_sample_rows':table.rows[table.header_row_count:table.header_row_count+3]})
    doc = build_document(str(path),texts,[[]for _ in texts],path.stat().st_size)
    doc.parse_artifact=artifact;doc.fact_context=context;doc.document_profile=profile
    year_issues=[vars(i)for i in BUD003_YearConsistency().apply(doc)]
    with fitz.open(path) as pdf:
        for number in pages:
            pdf[number-1].get_pixmap(dpi=120).save(target/f'page-{number}.png')
    result={'doc_id':doc_id,'filename':path.name,'source_sha256':digest,'pages':artifact.page_count,
        'profile_kind':profile.kind,'profile_year':profile.year,'profile_org':profile.organization_name.value,
        'first_three_pages_text':texts[:3],'total_facts':len(context.facts),
        'status_counts':dict(Counter(f.recognition_status for f in context.facts)),
        'tables':tables,'year_issues':year_issues,'elapsed_seconds':round(time.monotonic()-started,3),
        'source_unchanged':hashlib.sha256(path.read_bytes()).hexdigest()==digest}
    (target/'receipt.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str))
    results.append(result)
    print(json.dumps({'doc':doc_id,'facts':len(context.facts),'organization':result['profile_org'],
        'tables':[(t['page'],t['code'],t['fact_count'])for t in tables],
        'year_issues_p33':sum(i.get('location',{}).get('page')==33 for i in year_issues)},ensure_ascii=False),flush=True)
(OUT/'fresh_native_reproduction.json').write_text(json.dumps({'current_code_files_compared':len(baseline),
    'changed_code_files':changed,'scope':'read_only_native_extraction_no_OCR_or_database',
    'results':results},ensure_ascii=False,indent=2,default=str))
