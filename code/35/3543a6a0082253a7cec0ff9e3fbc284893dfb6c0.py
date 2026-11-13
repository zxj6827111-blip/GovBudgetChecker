"""Finite synthetic parse contracts through production materialization/rule code.

No model, HTTP, DB, benchmark corpus, or human truth claims. Writes this audit only.
"""
import dataclasses
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.engine.fact_context import build_fact_context_from_artifact, resolve_operand
from src.engine.pipeline import run_rules_with_outcomes
from src.engine.rules_v33 import R33103_Income_vs_Text, build_document
from src.services.pdf_parser import PDFParser
from tests.remediation.test_rc_batch1 import _make_fragment, _artifact_with

PROTECTED = [
    'src/services/fiscal_fact_materializer.py', 'src/engine/fact_context.py',
    'src/engine/rules_v33.py', 'src/engine/budget_rules.py', 'src/engine/common_rules.py',
    'src/services/ocr_qualification.py', 'src/services/pdf_parser.py',
    'src/services/ocr_fact_promotion.py', 'src/services/fiscal_row_roles.py',
]


def hashes():
    return {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in PROTECTED}


def observe(table_owner, unit, raw='900', owner='测试甲局'):
    rows = [['科目名称', '本年收入合计', '财政拨款收入'], ['合计', raw, raw]]
    fragment = _make_fragment('FIN_02_income', rows, title=f'{table_owner}收入决算表',
                              unit_hint=unit, page=2)
    instance = PDFParser(None)._build_logical_instance(fragment)
    artifact = _artifact_with([fragment], [instance])
    context = build_fact_context_from_artifact(artifact, org_name=owner, fiscal_year=2025,
                                               report_kind='final')
    resolution = resolve_operand(context, 'total_actual', table_code='FIN_02_income',
                                 report_year=2025, budget_stages=('决算',))
    doc = build_document('测试甲局2025年决算.pdf', [
        '测试甲局2025年部门决算\n二、收入决算情况说明\n'
        '本年收入合计100万元，财政拨款收入100万元，占100%。',
        fragment.title + ('\n单位：'+unit if unit else ''),
    ], [[], [rows]], 100)
    doc.fact_context = context
    issues, outcomes = run_rules_with_outcomes(doc, report_kind='final', rules=[R33103_Income_vs_Text()])
    return {
        'table_owner_input': table_owner, 'parsed_table_owner': instance.org_name,
        'context_owner': context.org_name, 'unit': unit, 'table_raw': raw,
        'facts': [fact.to_dict() for fact in context.facts],
        'operand_resolution': {'status': resolution.status, 'value': str(resolution.value),
                               'reason': resolution.reason, 'candidate_uids': resolution.candidate_uids},
        'issues': [dataclasses.asdict(issue) for issue in issues],
        'outcomes': [outcome.to_dict() for outcome in outcomes],
        'receipts': doc.fact_receipts,
    }


def main():
    output = Path(sys.argv[1]).resolve()
    before = hashes()
    cases = {
        'same_owner_wan_amount_mismatch': observe('测试甲局', '万元'),
        'same_owner_yuan_amount_mismatch': observe('测试甲局', '元'),
        'same_owner_no_unit': observe('测试甲局', None),
        'foreign_owner_amount_mismatch': observe('测试乙局', '万元'),
        'foreign_owner_same_amount': observe('测试乙局', '万元', '100'),
        'implicit_table_owner_same_amount': observe('', '万元', '100'),
    }
    after = hashes()
    result = {'scope': 'finite synthetic production parse/materialization/rule contracts',
              'source_hashes_before': before, 'source_hashes_after': after,
              'source_unchanged_during_probe': before == after, 'cases': cases}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str)+'\n')
    print(json.dumps({'output': str(output), 'source_unchanged': before == after,
                      'outcomes': {key: value['outcomes'] for key, value in cases.items()}}, ensure_ascii=False))


if __name__ == '__main__':
    main()
