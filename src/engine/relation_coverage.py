"""关系归属与完整性契约。已收到的回执子集不能代表整个检查完成。"""
from __future__ import annotations

RETIRED_OBLIGATION_ALIASES = {'OBL-TXT-T7': 'OBL-SG-TABLE-TEXT'}


def canonical_obligation_id(value):
    return RETIRED_OBLIGATION_ALIASES.get(str(value or ''), str(value or ''))


def canonical_session_decisions(rows):
    """只映射调用方已筛选的当前会话；冲突决定保持待处理，不写回历史。"""
    result = {str(r['obligation_id']): dict(r) for r in rows if r.get('obligation_id')}
    grouped = {}
    for row in rows:
        if row.get('obligation_id'):
            grouped.setdefault(canonical_obligation_id(row['obligation_id']), []).append(dict(row))
    for key, values in grouped.items():
        decisions = {str(row.get('decision') or 'pending') for row in values}
        if len(decisions) > 1:
            result[key] = {'obligation_id': key, 'decision': 'pending',
                           'alias_conflict': True, 'original_decisions': values}
        elif key not in result:
            result[key] = {**values[0], 'obligation_id': key,
                           'original_obligation_id': values[0]['obligation_id']}
    return result


def relation_owners(raw, fallback=()):
    if raw.get('obligation_id'):
        return [canonical_obligation_id(raw['obligation_id'])]
    rule, sub = raw.get('rule_id'), str(raw.get('sub_check_id') or '')
    if rule == 'V33-224':
        return ['OBL-SG-SHARE' if sub.startswith(('three_public:share:', 'three_public:percentage_scope'))
                else 'OBL-SG-TABLE-TEXT']
    if rule == 'V33-SG-COMPLETION':
        return ['OBL-SG-COUNTS' if sub.startswith('three_public:count:') else 'OBL-SG-COMPLETION']
    if rule == 'BUD-115':
        return ['OBL-SG-COUNTS']
    if rule == 'V33-250':
        return ['OBL-PERFORMANCE-COUNTS']
    return list(dict.fromkeys(canonical_obligation_id(x) for x in fallback))


def close_relation_scope(rule, doc, start, scope, obligation_id, unresolved=(), *, empty_applicable=False):
    """核对器完成全部声明遍历后封账；清单随回执保存，消费端再逐条对账。"""
    from .rules_v33 import _v33_relation_receipt
    records = [r for r in (getattr(doc, 'fact_receipts', []) or [])[start:]
               if r.get('rule_id') == rule.code]
    for record in records:
        record['obligation_id'] = obligation_id
    statuses = [r.get('status') for r in records]
    missing = [s for s in statuses if s not in {'pass', 'fail', 'not_applicable'}]
    status = ('insufficient_data' if unresolved or missing else 'fail' if 'fail' in statuses
              else 'not_applicable' if records and all(s == 'not_applicable' for s in statuses)
              else 'pass' if records or empty_applicable else 'not_applicable')
    _v33_relation_receipt(doc, rule.code, f'three_public:{scope}_scope', status=status,
        obligation_id=obligation_id, required_sub_check_ids=[r['sub_check_id'] for r in records],
        formula=f'all applicable {scope} relations accounted for',
        incomplete_reason='；'.join(unresolved) if unresolved else None,
        disclosure_sources=[source for r in records for source in r.get('disclosure_sources') or []],
        evidence_nature='coverage_checkpoint')


def scoped_rule_status(obligation_id, rule_id, records, kind):
    """None 表示旧调用未提供契约；六态值表示本义务实际消费的子检查结果。"""
    owned = [r for r in records if r.get('rule_id') == rule_id
             and obligation_id in relation_owners(r)]
    expected = None
    if obligation_id == 'OBL-SG-COUNTS':
        fields = ['purchase_count', 'vehicle_stock']
        if kind == 'final':
            fields += ['overseas_groups', 'overseas_people', 'reception_batches', 'reception_people']
        expected = {'three_public:count:' + x for x in fields}
    elif obligation_id == 'OBL-SG-COMPLETION':
        expected = {'three_public:disclosure:' + x for x in ['abroad', 'car_buy', 'car_run', 'reception']}
    elif rule_id in {'V33-224', 'CMM-001'} and obligation_id in {'OBL-SG-TABLE-TEXT', 'OBL-SG-SHARE'}:
        checkpoint = 'three_public:' + ('percentage' if obligation_id == 'OBL-SG-SHARE' else 'amount') + '_scope'
        markers = [r for r in owned if r.get('sub_check_id') == checkpoint]
        if not markers:
            empty = [r for r in owned if r.get('sub_check_id') == 'three_public:explicit_empty']
            return 'not_applicable' if empty and all(r['status'] == 'not_applicable' for r in empty) else 'insufficient_data'
        if len(markers) != 1:
            return 'insufficient_data'
        expected = {checkpoint, *(markers[0].get('required_sub_check_ids') or [])}
    if expected is None:
        return None
    observed = {str(r.get('sub_check_id')) for r in owned}
    if not expected <= observed:
        return 'insufficient_data'
    states = [r.get('status') for r in owned if r.get('sub_check_id') in expected]
    if any(s not in {'pass', 'fail', 'not_applicable'} for s in states):
        return 'insufficient_data'
    if not states:
        return 'insufficient_data'
    return 'fail' if 'fail' in states else 'not_applicable' if all(s == 'not_applicable' for s in states) else 'pass'
