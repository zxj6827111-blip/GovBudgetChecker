"""Explicit narrative evidence is required to disambiguate a generic budget header."""
from __future__ import annotations

import re
from decimal import Decimal

from .rule_policy import amount_difference_exceeds_policy


def phase_reason(doc, fact, stage, indicator):
    if stage == '决算' or fact is None:
        return None
    header = re.sub(r'\s+', '', str(fact.extra_dims.get('source_header') or ''))
    if not header or any(word in header for word in ('年初预算', '全年预算', '调整后预算', '调整预算')):
        return None
    if '预算数' not in header:
        return None
    from .native_table_text import sections, context_guard
    from .native_three_public_ratios import subject_before
    from .native_narrative_checks import _NUMBER, _UNIT, _wan
    text, ranges = sections(doc, lambda title: '三公' in title and '决算' in title)
    amounts = set()
    total_amounts = set()
    pattern = re.compile(rf'(?P<phase>年初预算|全年预算|调整后预算)(?:数)?(?:为|是)?'
                         rf'(?P<amount>{_NUMBER})(?P<unit>{_UNIT})')
    for _, begin, end in ranges:
        for match in pattern.finditer(text.compact, begin, end):
            subject, _ = subject_before(text.compact[begin:match.start()])
            if match.group('phase') == stage and not context_guard(doc, text.compact, match.start(), match.end()):
                value = _wan(match.group('amount'), match.group('unit'))
                if subject == indicator:
                    amounts.add(value)
                if subject == 'total_actual':
                    total_amounts.add(value)
    if len(amounts) == 1 and not amount_difference_exceeds_policy(next(iter(amounts)) - fact.normalized_value):
        return None
    # A horizontal 三公 matrix has one shared budget-header identity. Its
    # explicitly phased overall amount can confirm that identity for the
    # component columns only when it matches this same instance's total.
    context = getattr(doc, 'fact_context', None)
    totals = [f for f in getattr(context, 'facts', [])
              if f.table_code == fact.table_code and f.instance_uid == fact.instance_uid
              and f.indicator == 'total_actual' and f.subject_code == 'total'
              and f.budget_stage == stage and f.fiscal_year == fact.fiscal_year]
    from .fact_context import operand_rejection_reason
    if (indicator != 'total_actual' and len(total_amounts) == 1 and len(totals) == 1
            and not operand_rejection_reason(totals[0], context.org_name)
            and '预算数' in str(totals[0].extra_dims.get('source_header') or '')
            and not amount_difference_exceeds_policy(next(iter(total_amounts)) - totals[0].normalized_value)):
        return None
    return '表头仅披露“预算数”，未取得与原文一致的显式预算阶段证据，请复核年初/全年/调整后预算口径'
