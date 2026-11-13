"""只读方案审核：原件重新解析与五条规则的局部调用，不写业务库/上传目录。

这不是全流水线验收，不评价前端、持久化、完整规则覆盖或上线资格。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import time
from collections import Counter

ROOT = Path('/Volumes/workSSD/software development/GovBudgetChecker')
MAIN = ROOT / 'artifacts/daily-main'
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(MAIN))
os.environ.pop('GOVBUDGET_EMPTY_VALUE_CONVENTIONS', None)

import pdfplumber
from src.services.pdf_parser import PDFParser
from src.engine.fact_context import build_fact_context_from_artifact, _column_mappings_for_instance, _fragment_for_instance
from src.engine.pipeline import build_document
from src.engine.rule_identity import rule_identity_requirements
from src.engine.rule_outcome import RuleOutcomeSignal
from src.engine.rules_v33 import (
    R33005_TableTotalConsistency, R33104_Expense_vs_Text,
    R33222_Narrative5_T5, R33240_Table2_IncomeAdvancedCheck, R33CrossSanGongEcon,
)
from src.engine.native_empty_operands import blank_term
from src.engine.diagnostic_fields import _section_page

SAMPLES = [
    ('79efdd8f0d7b3a2938de65cfb5aad101', [R33104_Expense_vs_Text, R33CrossSanGongEcon]),
    ('b589d6bd4436edaa140a224135bf10d0', [R33240_Table2_IncomeAdvancedCheck]),
    ('02e4fb64ab6bbf325117d325a2dcbfa4', [R33222_Narrative5_T5]),
    ('f1a4c441a3bd02ec4cce465b1739f046', [R33005_TableTotalConsistency]),
]


def dump(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + '\n')


def run_rule(doc, cls):
    rule = cls()
    called = set()
    touched = set()
    target = str(MAIN / 'src/engine/rules_v33.py')

    def trace(frame, event, arg):
        filename = frame.f_code.co_filename
        if event == 'call' and filename.startswith(str(MAIN / 'src/engine')):
            called.add((str(Path(filename).relative_to(MAIN)), frame.f_code.co_name))
        if event == 'line' and filename == target:
            touched.add(frame.f_lineno)
        return trace

    result = {'rule_id': rule.code}
    start = len(getattr(doc, 'fact_receipts', []) or [])
    sys.settrace(trace)
    try:
        doc.current_rule_code = rule.code
        with rule_identity_requirements(doc, rule.code):
            issues = rule.apply(doc)
        result.update(status='returned', issues=[{'message': i.message, 'severity': i.severity} for i in issues])
    except RuleOutcomeSignal as exc:
        result.update(status=exc.status, detail=exc.detail,
                      unresolved=list(exc.unresolved_reasons or []),
                      issues=[{'message': i.message, 'severity': i.severity} for i in exc.partial_issues])
    except Exception as exc:
        result.update(status='probe_error', error=f'{type(exc).__name__}: {exc}')
    finally:
        sys.settrace(None)
    result['calls'] = sorted(called)
    result['plan_target_line_hits'] = {str(line): line in touched for line in [1732, 1760, 1784, 3354, 6798, 6915, 10683]}
    result['receipts'] = list(getattr(doc, 'fact_receipts', []) or [])[start:]
    result['receipt_statuses'] = dict(Counter(r.get('status') for r in result['receipts']))
    return result


def main():
    summary = []
    for job, classes in SAMPLES:
        start = time.monotonic()
        folder = ROOT / 'uploads' / job
        status = json.loads((folder / 'status.json').read_text())
        pdf_path = folder / status['filename']
        print('parsing', job, pdf_path.name, flush=True)
        with pdfplumber.open(pdf_path) as pdf:
            artifact = PDFParser(None).build_parse_artifact_from_handle(
                pdf, file_hash=hashlib.sha256(pdf_path.read_bytes()).hexdigest(), pdf_path=str(pdf_path))
            texts = [page.extract_text() or '' for page in pdf.pages]
        page_tables = [[] for _ in texts]
        for table in artifact.physical_fragments:
            if 1 <= table.page_number <= len(page_tables):
                page_tables[table.page_number - 1].append(table.rows)
        context = build_fact_context_from_artifact(
            artifact, org_name=status['organization_name'], fiscal_year=status['fiscal_year'],
            report_kind='final', doc_type=status.get('doc_type'))
        doc = build_document(str(pdf_path), texts, page_tables, pdf_path.stat().st_size)
        doc.fact_context, doc.parse_artifact = context, artifact
        doc.report_kind = 'final'
        probes = [run_rule(doc, cls) for cls in classes]
        tables = []
        for instance in artifact.logical_tables:
            table = _fragment_for_instance(artifact, instance)
            if table is not None and instance.table_code in {'FIN_02_income','FIN_03_expenditure','FIN_06_basic_expenditure','FIN_07_three_public'}:
                mappings = _column_mappings_for_instance(instance, table)
                tables.append({'table_code': instance.table_code, 'pages': [instance.page_start,instance.page_end],
                               'mappings': mappings, 'blank_count': sum(c.native_blank_verified for c in table.cells)})
        extra = {'missing_text_fallback_page': _section_page(doc, '说明文本中未提取到项目支出')}
        if job.startswith('b589'):
            extra['income_blank_term_default'] = {
                indicator: blank_term(doc, 'FIN_02_income', indicator, code='total') is not None
                for indicator in ['superior_subsidy_income','operational_income','subsidiary_income']}
        if job.startswith('79ef'):
            extra['native_three_public_rows'] = [
                {'label': row.label, 'row_role': row.row_role, 'amounts': [str(c.number) for c in row.cells]}
                for table in getattr(doc, 'parsed_tables', {}).values() if table.table_code == 'FIN_07_three_public'
                for row in table.rows]
        data = {'scope':'fresh main parser + selected rules only; no database, AI, OCR, full pipeline or acceptance',
                'job_id': job, 'pdf': str(pdf_path), 'pdf_sha256': artifact.source_file_hash,
                'parse_run_uid': artifact.parse_run_uid, 'fact_count': len(context.facts) if context else 0,
                'tables': tables, 'extra': extra, 'probes': probes, 'elapsed_seconds':round(time.monotonic()-start,2)}
        dump(f'{job}.json', data)
        compact = {'job':job,'elapsed_seconds':data['elapsed_seconds'], 'extra':extra,
                   'rules':[{'rule_id':p['rule_id'],'status':p['status'],'detail':p.get('detail',''),
                             'receipt_statuses':p['receipt_statuses'],'target_hits':p['plan_target_line_hits'],
                             'error':p.get('error')} for p in probes]}
        summary.append(compact)
        dump('main_probe_summary.json', summary)
        print(json.dumps({**compact,'extra':{'missing_text_fallback_page':extra['missing_text_fallback_page']}},ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
