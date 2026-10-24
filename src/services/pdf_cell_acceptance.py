"""逐出现次数、完整身份和物理格验收；未恢复格继续进入覆盖率分母。"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from src.services.analysis_fact_snapshot import validate_snapshot,canonical_facts
from src.engine.fiscal_lexeme import amount_in_wan


IDENTITY_FIELDS=('org_name','org_scope','fiscal_year','period','fund_scope','budget_stage',
                 'table_code','indicator','subject_code','subject_name')


def non_amount_cell_identity(artifact,snapshot,logical):
    """只恢复已有格的身份；读取分类标签时不生成金额事实或补零。"""
    from src.engine.fact_context import _fragment_for_instance,_column_mappings_for_instance,_stage_for_fact
    from src.services.fiscal_fact_materializer import FiscalFactMaterializer,measure_fund_scope
    context=snapshot['effective_context']
    instances=[i for i in artifact.logical_tables if i.instance_uid==logical.table_instance_uid
               or logical.table_instance_uid in i.fragment_uids]
    if len(instances)!=1 or instances[0].identity_conflict:return None
    instance=instances[0];table=_fragment_for_instance(artifact,instance)
    if table is None:return None
    columns=[m for m in _column_mappings_for_instance(instance,table)if m['source_col_idx']==logical.col_idx]
    if len(columns)!=1:return None
    mapping=columns[0]
    cells=[c for c in table.cells if c.row_idx==logical.row_idx and c.col_idx==logical.col_idx]
    if len(cells)!=1:return None
    cell=cells[0]
    if logical.value_kind=='blank':
        if cell.extraction_method!='ocr'and not cell.native_blank_verified:return None
        if cell.extraction_method=='ocr':
            from src.services.ocr_grid_reconstruction import bound_grid_table
            observed=cell.ocr_provenance.get('cell')or{}
            if observed.get('literal_status')!='blank':return None
            sources=[c for c in artifact.ocr_table_candidates
                     if c.provenance==cell.ocr_provenance.get('source')]
            if len(sources)!=1 or bound_grid_table(sources[0],artifact)[1]:return None
    labels=mapping.get('source_label_cols')
    if labels is None:labels=list(range(min(m['source_col_idx']for m in _column_mappings_for_instance(instance,table))))
    classification=FiscalFactMaterializer(None)._extract_classification(
        [{'raw_text':c.raw_text}for c in sorted(table.cells,key=lambda c:c.col_idx)
         if c.row_idx==logical.row_idx and c.col_idx in labels])
    if not classification:return None
    from src.services.fiscal_source_scope import organization_scope
    from src.engine.fact_context import _owner_rejection_reason
    from types import SimpleNamespace
    local_scope=organization_scope(artifact,local_title=table.title)
    if (_owner_rejection_reason(SimpleNamespace(org_name=instance.org_name,
            extra_dims={'source_table_title':table.title}),context.get('org_name'))
        and not any(word in table.title for word in ('部门预算','部门决算','单位预算','单位决算'))):
        return None
    identity={'org_name':instance.org_name or context.get('org_name'),'org_scope':local_scope,
        'fiscal_year':instance.fiscal_year or context.get('fiscal_year'),'period':'本年',
        'fund_scope':measure_fund_scope(table.table_code,table.title,mapping.get('source_col_name'))[0],
        'budget_stage':_stage_for_fact(mapping['canonical_measure'],mapping.get('source_col_name',''),
                                     context.get('report_kind'),context.get('budget_stage')),
        'table_code':table.table_code,'indicator':mapping['canonical_measure'],
        'subject_code':classification['classification_code'],'subject_name':classification['classification_name'],
        'column_identity':mapping.get('source_col_name')}
    required=('org_name','org_scope','fiscal_year','period','fund_scope','budget_stage','table_code','indicator')
    if any(not identity.get(key)for key in required)or not(identity['subject_code']or identity['subject_name']):
        return None
    return identity


def evaluate_cells(truth,snapshot,artifact=None):
    validate_snapshot(snapshot)
    if truth.get('source_file_hash')!=snapshot['source_file_hash']:
        raise ValueError('truth original hash differs from snapshot original')
    if artifact is not None:
        from src.services.input_processing_identity import artifact_content_hash
        if artifact_content_hash(artifact)!=snapshot['artifact_content_hash']:
            raise ValueError('cell evidence artifact differs from accepted snapshot')
    entries=truth.get('cells')or[]
    if len({c['occurrence_id']for c in entries})!=len(entries):raise ValueError('duplicate truth occurrence ID')
    if len({(c['physical_page'],tuple(c['bbox']),c.get('column_identity'))for c in entries})!=len(entries):
        raise ValueError('duplicate truth physical cell identity')
    facts=canonical_facts(snapshot['effective_context'].get('facts',[]));by_anchor=defaultdict(list)
    for fact in facts:
        anchors={(r.get('physical_page'),tuple(r.get('bbox')or[]))for r in fact['extra_dims'].get('evidence_refs',[])}
        for anchor in anchors:by_anchor[anchor].append(fact)
    correct=[];missing=[];wrong=[];unreadable=[];matched=set();readable=[]
    for expected in entries:
        if expected.get('legibility')=='unreadable':unreadable.append(expected['occurrence_id']);continue
        readable.append(expected)
        observed=by_anchor.get((expected['physical_page'],tuple(expected['bbox'])),[])
        admitted=[f for f in observed if f['recognition_status']in {'unverified','verified'}]
        if expected.get('literal_kind')in {'blank','dash','not_applicable'}:
            if admitted:wrong.append({'occurrence_id':expected['occurrence_id'],'reason':'amount_in_non_amount_cell'})
            else:
                cells=[c for c in (getattr(artifact,'resolved_cells',[])or[])
                    if c.physical_page==expected['physical_page']and c.bbox==expected['bbox']
                    and c.value_kind==expected['literal_kind']and c.resolution_status=='source_bound'
                    and c.semantic_identity.get('table_code')==expected['table_code']
                    and c.semantic_identity.get('indicator')==expected['indicator']]
                identity=non_amount_cell_identity(artifact,snapshot,cells[0])if len(cells)==1 else None
                if identity and all(identity.get(k)==expected.get(k)for k in (*IDENTITY_FIELDS,'column_identity')):
                    correct.append(expected['occurrence_id'])
                else:missing.append({'occurrence_id':expected['occurrence_id'],'reason':'physical_blank_requires_cell_truth'})
            continue
        if len(admitted)!=1:
            (wrong if len(admitted)>1 else missing).append({'occurrence_id':expected['occurrence_id'],
                'reason':'non_unique_admitted_fact'if len(admitted)>1 else'not_automatically_recovered'})
            continue
        fact=admitted[0];matched.add(fact['fact_uid'])
        value=amount_in_wan(expected['raw_value'],expected['unit'])
        failures=[key for key in IDENTITY_FIELDS if fact.get(key)!=expected.get(key)]
        if expected.get('column_identity')!=fact['extra_dims'].get('source_header'):
            failures.append('column_identity')
        if value is None or fact['normalized_value']is None or Decimal(fact['normalized_value'])!=value:
            failures.append('amount')
        if expected['unit']!=fact['unit']:failures.append('unit')
        if failures:wrong.append({'occurrence_id':expected['occurrence_id'],'fact_uid':fact['fact_uid'],'fields':failures})
        else:correct.append(expected['occurrence_id'])
    extra=[f['fact_uid']for f in facts if f['recognition_status']in {'unverified','verified'}and f['fact_uid']not in matched
           and truth.get('annotation_complete')is True]
    denominator=len(readable)
    return {'source_file_hash':snapshot['source_file_hash'],'snapshot_hash':snapshot['snapshot_hash'],
        'readable_occurrences':denominator,'unreadable_occurrences':unreadable,'correct_occurrences':len(correct),
        'correct_auto_recovery_coverage':len(correct)/denominator if denominator else None,
        'wrong_admissions':wrong,'unrecovered':missing,'extra_admitted_fact_uids':extra,
        'zero_wrong_admissions':not wrong and not extra,
        'independent_truth_confirmed':truth.get('split')=='independent_acceptance'
            and truth.get('review_status')=='human_verified'and truth.get('production_output_used_as_truth')is False
            and truth.get('annotation_complete')is True and denominator>0,
        'accuracy_gate_met':False}


def acceptance_suite(records):
    """材料配额、逐组覆盖率与零错误准入同时满足才通过。"""
    unique={r['source_file_hash']:r for r in records}
    if len(unique)!=len(records):raise ValueError('acceptance source hashes must be unique')
    budgets=[r for r in records if r.get('report_kind')=='budget'];finals=[r for r in records if r.get('report_kind')=='final']
    thresholds={'native':.999,'clear_scan':.995,'difficult_readable':.98};groups={}
    for key,minimum in thresholds.items():
        items=[r for r in records if r.get('material_group')==key]
        denominator=sum(r['readable_occurrences']for r in items);numerator=sum(r['correct_occurrences']for r in items)
        coverage=numerator/denominator if denominator else None
        groups[key]={'denominator':denominator,'correct':numerator,'coverage':coverage,'threshold':minimum,
                     'passed':coverage is not None and coverage>=minimum}
    quota=(len(records)>=30 and len(budgets)>=15 and len(finals)>=15
           and sum(r.get('material_group')=='native'for r in records)>=12
           and sum(r.get('material_group')in {'clear_scan','difficult_readable'}for r in records)>=18
           and len({r.get('region')for r in records if r.get('region')})>=3
           and len({r.get('year')for r in records if r.get('year')})>=3)
    return {'quota_met':quota,'groups':groups,'accepted':quota and all(g['passed']for g in groups.values())
        and all(r['independent_truth_confirmed']and r['zero_wrong_admissions']for r in records)}
