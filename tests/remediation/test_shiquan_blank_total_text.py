from src.engine.native_empty_operands import NativeBlankTerm
from src.engine.native_final_disclosure import _ITEM, _amount, _hierarchy_fact
from src.engine.native_narrative_checks import _Text
from src.engine.rules_v33 import R33222_Narrative5_T5
from tests.remediation.test_shiquan_native_empty_rows import grid_document


def document():
    return grid_document([
        ['功能分类科目编码', '科目名称', '合计', '基本支出', '项目支出'],
        ['合计', '', '10.00', '10.00', ''],
        ['205', '教育支出', '', '10.00', ''],
        ['20501', '普通教育', '', '10.00', ''],
        ['2050102', '小学教育', '', '10.00', ''],
    ], table_code='FIN_05_general_public_expenditure')


def test_exact_name_chain_can_compare_certified_blank_total_without_creating_zero_fact():
    doc = document()
    before = doc.fact_context.to_dict()
    text = _Text.from_pages(['教育支出(类)普通教育(款)小学教育(项)支出决算10.00万元'])
    item = _ITEM.search(text.compact)
    fact, error = _hierarchy_fact(doc, doc.fact_context.facts, item)
    assert error is None and isinstance(fact, NativeBlankTerm)
    rule = R33222_Narrative5_T5()
    issues = _amount(rule, doc, text, 0, len(text.compact), '10.00', '万元',
                     fact, 'exact_leaf', '小学教育', '说明五')
    assert len(issues) == 1 and '空白' in issues[0].message
    assert issues[0].location['native_blank_sources'][0]['subject_code'] == '2050102'
    assert issues[0].location['operand_refs'] == []
    assert before == doc.fact_context.to_dict()
    from src.services.rule_source_binding import bind_rule_sources
    doc.page_texts = [text.raw]
    issue = issues[0]
    finding = {'rule_id': rule.code, 'source': 'rule', 'severity': 'error',
               'location': issue.location, 'evidence': []}
    result = {'rule_findings': [finding]}
    assert bind_rule_sources(result, page_texts=doc.page_texts,
        source_file_hash=doc.fact_context.file_hash, artifact=doc.parse_artifact,
        fact_context=doc.fact_context, consumption=doc.structured_consumption)['bound_rule_findings'] == 1
    assert finding['severity'] == 'error' and finding['fact_ids'] == []
    finding['location']['native_blank_sources'][0]['bbox'][0] += 1
    assert bind_rule_sources(result, page_texts=doc.page_texts,
        source_file_hash=doc.fact_context.file_hash, artifact=doc.parse_artifact,
        fact_context=doc.fact_context, consumption=doc.structured_consumption)['bound_rule_findings'] == 0


def test_uncertified_leaf_total_still_cannot_be_used_as_zero():
    doc = document()
    table = doc.parse_artifact.table_fragments[0]
    next(c for c in table.cells if (c.row_idx, c.col_idx) == (4, 2)).native_blank_verified = False
    item = _ITEM.search('教育支出(类)普通教育(款)小学教育(项)')
    fact, error = _hierarchy_fact(doc, doc.fact_context.facts, item)
    assert fact is None and error
