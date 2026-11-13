"""从当前公开解析产物运行规则，候选与来源真值分别保存。"""
from dataclasses import asdict
import json
from pathlib import Path
import sys

import pdfplumber

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
sys.path.insert(0,str(ROOT))
from src.schemas.parse_artifact import ParseArtifact
from src.services.document_profile_resolver import resolve_document_profile
from src.engine.fact_context import build_fact_context_from_artifact
from src.engine.pipeline import build_document, run_rules_with_outcomes
from src.services.structure_view import build_structure_view


for doc in json.loads((BASE / 'inventory.json').read_text()):
    folder = BASE / 'documents' / doc['doc_id']
    artifact = ParseArtifact.from_dict(json.loads((folder / 'artifact.json').read_text()))
    with pdfplumber.open(folder / 'source.pdf') as pdf:
        texts = [page.extract_text() or '' for page in pdf.pages]
    profile = resolve_document_profile(filename=doc['filename'], page_texts=texts)
    context = build_fact_context_from_artifact(artifact, org_name=profile.organization_name.value,
                                              fiscal_year=profile.year, report_kind=profile.kind)
    tables = [[] for _ in texts]
    for table in artifact.physical_fragments:
        if table.extraction_method != 'page_text_fallback':
            tables[table.page_number-1].append(table.rows)
    current = build_document(doc['filename'],texts,tables,(folder / 'source.pdf').stat().st_size)
    current.parse_artifact = artifact
    current.fact_context = context
    current.document_profile = profile
    current.structure_view = build_structure_view(texts,artifact)
    issues,outcomes = run_rules_with_outcomes(current,report_kind=profile.kind)
    for name,data in [('engine_issues',[asdict(issue) for issue in issues]),
                      ('rule_outcomes',[asdict(outcome) for outcome in outcomes]),
                      ('check_receipts',getattr(current,'fact_receipts',[]))]:
        (folder / f'{name}.json').write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str))
    print(doc['doc_id'],'issues',len(issues),'noninfo',sum(issue.severity!='info' for issue in issues),flush=True)
