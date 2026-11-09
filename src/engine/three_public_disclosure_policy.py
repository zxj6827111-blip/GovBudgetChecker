"""三公说明的零变动、零发生额适用性；不把未知取数解释为零。"""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal
from types import SimpleNamespace

from .fact_context import _canonical_budget_stage, _fragment_for_instance, _owner_rejection_reason, operand_rejection_reason
from .native_empty_operands import NativeBlankTerm, blank_term, cell_id


_TABLE = 'FIN_07_three_public'
_NUMBER = r'(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?'
_MOVEMENT = re.compile(r'增加|增长|减少|下降')
_ZERO_DELTA = re.compile(rf'(?:增加|增长|减少|下降)(?P<value>{_NUMBER})(?:亿元|万元|元|%)?(?![\d.])')


def movement_is_explicit_zero(clause: str) -> bool:
    """只认紧随方向词的零增减额；“减少为0/降至0”仍是变动。"""
    compact = re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(clause or '')))
    moves = list(_MOVEMENT.finditer(compact))
    if not moves:
        return False
    for move in moves:
        match = _ZERO_DELTA.match(compact, move.start())
        if match is None or Decimal(match['value'].replace(',', '')) != 0:
            return False
    # 同一分句还给出正数时，不能让零短语掩盖矛盾；年度本身不是增减额。
    remainder = _ZERO_DELTA.sub('', compact)
    remainder = re.sub(r'20\d{2}(?:年度|年)', '', remainder)
    if re.search(r'高于|低于|超出|超过|少于|(?:降|减|增|升)(?:至|到|为)', remainder):
        return False
    return not any(Decimal(match.group().replace(',', '')) != 0
                   for match in re.finditer(_NUMBER, remainder))


def _positive_reception_claim(scope: str) -> bool:
    """只看接待本项；预算或明确上年金额不当作本年决算矛盾。"""
    from .native_narrative_checks import _UNIT

    compact = re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(scope or '')))
    # 千分位逗号属于金额，不能切成“1”和“000万元”后漏掉正额矛盾。
    compact = re.sub(r'(?<=\d),(?=\d{3}(?:\D|$))', '', compact)
    subjects = re.compile(r'国内公务接待|公务接待|接待外宾|外宾接待|因公出国|公务用车|三公')
    reception = False
    for clause in re.split(r'[，,。；;！？!?]', compact):
        anchors = list(subjects.finditer(clause))
        # 同一分句可能同时列几项，按每个金额之前最近的业务主体归属。
        for amount in re.finditer(rf'(?P<amount>{_NUMBER})(?P<unit>{_UNIT}|万)', clause):
            before = [anchor for anchor in anchors if anchor.end() <= amount.start()]
            local_reception = reception
            prefix = clause[:amount.start()]
            if before:
                nearest = before[-1]
                local_reception = nearest.group() in {'国内公务接待', '公务接待', '接待外宾', '外宾接待'}
                prefix = clause[nearest.end():amount.start()]
            if not local_reception or Decimal(amount['amount'].replace(',', '')) <= 0:
                continue
            # “预算为1万元、决算0万元”允许共存；未确认阶段的正数保守拒绝豁免。
            if ('预算' in prefix and '决算' not in prefix) or re.search(r'上年|去年|上年度', prefix):
                continue
            return True
        if anchors:
            reception = anchors[-1].group() in {'国内公务接待', '公务接待', '接待外宾', '外宾接待'}
    return False


def reception_quantity_not_required(rule, doc, scope: str) -> bool:
    """仅当前原生FIN07的唯一合格接待决算零免数量，legacy不作额外推断。"""
    context, artifact = getattr(doc, 'fact_context', None), getattr(doc, 'parse_artifact', None)
    if (context is None or artifact is None or not getattr(context, 'org_name', None)
            or getattr(context, 'fiscal_year', None) is None or getattr(context, 'report_kind', None) != 'final'
            or not getattr(context, 'file_hash', None) or not getattr(context, 'parse_run_uid', None)
            or context.file_hash != getattr(artifact, 'source_file_hash', None)
            or context.parse_run_uid != getattr(artifact, 'parse_run_uid', None)):
        return False
    instances = [instance for instance in getattr(artifact, 'logical_tables', [])
                 if instance.table_code == _TABLE]
    if len(instances) != 1:
        return False
    instance = instances[0]
    table = _fragment_for_instance(artifact, instance)
    if (not instance.instance_uid or instance.identity_conflict or table is None
            or table.extraction_method not in {'lines', 'lines_strict'}
            or (instance.fiscal_year is not None and instance.fiscal_year != context.fiscal_year)
            or _owner_rejection_reason(SimpleNamespace(org_name=instance.org_name,
                 extra_dims={'source_table_title': table.title}), context.org_name)):
        return False
    facts = [fact for fact in context.facts if fact.table_code == _TABLE]
    if any(fact.instance_uid != instance.instance_uid for fact in facts):
        return False
    reception = [fact for fact in facts if fact.indicator == 'reception']
    if any(fact.subject_code != 'total' or _canonical_budget_stage(fact.budget_stage)
           not in {'年初预算', '全年预算', '调整后预算', '决算'} for fact in reception):
        return False
    actuals = [fact for fact in reception if _canonical_budget_stage(fact.budget_stage) == '决算']
    if len(actuals) > 1:
        return False
    if actuals:
        term = actuals[0]
        if (not term.fact_uid or operand_rejection_reason(term, context.org_name)
                or term.fiscal_year != context.fiscal_year or term.period not in (None, '本年')
                or term.fund_scope != 'fiscal_appropriation'
                or term.normalized_value != Decimal('0')):
            return False
    else:
        # 没有事实时只回看同一原件的接待决算格；预算/合计零不能填补它。
        term = blank_term(doc, _TABLE, 'reception', code='total', stage='决算')
        if term is None or term.normalized_value != Decimal('0'):
            return False
    if _positive_reception_claim(scope):
        return False

    from .native_table_arithmetic import receipt

    sources = []
    if not isinstance(term, NativeBlankTerm):
        for ref in (term.extra_dims or {}).get('evidence_refs', []):
            if ref.get('physical_page') and ref.get('source_cell_id'):
                originals = [cell for fragment in getattr(artifact, 'physical_fragments', [])
                             for cell in fragment.cells if cell_id(fragment, cell) == ref['source_cell_id']]
                if (ref.get('file_hash') != context.file_hash or len(originals) != 1
                        or originals[0].page_number != ref['physical_page']
                        or originals[0].extraction_method not in {'lines', 'lines_strict'}
                        or originals[0].is_header or originals[0].numeric_value != Decimal('0')):
                    return False
                sources.append({'page': ref['physical_page'], 'span': ref.get('quote', term.raw_value),
                                'role': '原生三公表公务接待决算零', 'file_hash': context.file_hash,
                                'source_cell_id': ref['source_cell_id'], 'bbox': ref.get('bbox')})
        if not sources:
            return False
    receipt(rule, doc, 'three_public:reception_quantity:zero_actual', [term],
            status='not_applicable', formula='admitted reception actual == 0; quantity disclosure not required',
            reason='原生三公表公务接待本年决算无发生额，按金额驱动口径无需披露批次人数',
            disclosure_sources=sources)
    return True
