"""款名真实异序错误不遮蔽金额；原格全集缺失或歧义绝不恢复身份。"""
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from src.engine.fact_context import FactContext
from src.engine.native_final_disclosure import _hierarchy_fact
from tests.remediation.test_r22_a2_identity_chain import _fact, _item_match, ORG, YEAR


def document(*, extra=()):
    entries = [('212', '城乡社区支出'), ('21201', '城乡社区管理\n事务'),
               ('2120199', '其他城乡社区\n管理事务支出'), *extra]
    cells, tokens, lines, facts = [], [], [], []
    for row, (code, name) in enumerate(entries):
        y = row * 30
        boxes = [(0, y, 50, y+25), (50, y, 150, y+25)]
        for col, (raw, box) in enumerate(zip((code, name), boxes)):
            cells.append(NS(row_idx=row, col_idx=col, raw_text=raw, page_number=1,
                            bbox=box, ocr_provenance={}, is_header=False))
            for i, part in enumerate(raw.splitlines()):
                tokens.append(NS(token_uid=f'{row}-{col}-{i}', source_method='native_text', physical_page=1,
                    bbox=(box[0]+1, y+1+i*10, box[2]-1, y+9+i*10), raw_text=part,
                    source_ref={'source_file_hash':'a'*64, 'source_block_id':f'{row}-{col}-{i}'}))
        lines.append(name.splitlines()[0] + '\n' + code + ' 100.00' +
                     ('\n'+name.splitlines()[1] if '\n' in name else ''))
        fact = _fact(code, name)
        fact.extra_dims['evidence_refs'][0]['physical_page'] = 1
        facts.append(fact)
    doc = NS(page_texts=['\n'.join(lines)], fact_context=FactContext(facts,'a'*64,'run',
             org_name=ORG, fiscal_year=YEAR), parse_artifact=NS(source_file_hash='a'*64,
             parse_run_uid='run', source_tokens=tokens, table_fragments=[NS(
                 table_code='FIN_05_general_public_expenditure', cells=cells,
                 extraction_method='lines')],logical_tables=[]))
    return doc, facts


def bind(doc, facts, parent='城乡社区事务管理'):
    aliases = []
    item = _item_match('城乡社区支出', parent, '其他城乡社区管理事务支出')
    fact, reason = _hierarchy_fact(doc, facts, item, aliases=aliases)
    return fact, reason, aliases


def test_wrapped_native_grid_binds_amount_without_correcting_printed_parent_name():
    doc, facts = document()
    fact, reason, sources = bind(doc, facts)
    assert reason is None and fact.subject_code == '2120199'
    assert facts[1].subject_name == '城乡社区管理\n事务'
    assert any(x['role'] == 'independent_hierarchy_chain_with_parent_word_order' for x in sources)
    assert any(x.get('span') == '管理事务支出' for x in sources)


@pytest.mark.parametrize('defect', ['missing_row', 'inline_code', 'continuation', 'continuation_header', 'repeated_code', 'altered_label',
                                   'wrong_bbox', 'foreign_run', 'ocr'])
def test_incomplete_or_uncertified_original_grid_never_certifies_identity(defect):
    doc, facts = document()
    if defect == 'missing_row': doc.page_texts[0] += '\n其他城乡社区管理事务支出\n2120299 100.00'
    elif defect == 'inline_code': doc.page_texts[0] += '\n其他项 2120299 其他城乡社区管理事务支出 100.00'
    elif defect == 'continuation': doc.page_texts.append('续表\n其他城乡社区管理事务支出\n2120299 100.00')
    elif defect == 'continuation_header': doc.page_texts.append('一般公共预算财政拨款支出决算表（续）\n其他城乡社区管理事务支出\n2120299 100.00')
    elif defect == 'repeated_code': doc.page_texts[0] += '\n其他城乡社区管理事务支出\n2120199 100.00'
    elif defect == 'altered_label': doc.parse_artifact.source_tokens[-1].raw_text = '管理服务支出'
    elif defect == 'wrong_bbox': doc.parse_artifact.table_fragments[0].cells[-1].bbox = (50,30,150,55)
    elif defect == 'foreign_run': doc.parse_artifact.parse_run_uid = 'old'
    else: doc.parse_artifact.table_fragments[0].cells[-1].ocr_provenance = {'candidate':'scan'}
    assert bind(doc, facts)[0] is None


def test_multiple_original_candidates_even_if_facts_drop_one_are_not_unique():
    doc, facts = document(extra=(('2120299','其他城乡社区\n管理事务支出'),))
    assert bind(doc, facts[:-1])[0] is None


def test_different_characters_in_parent_name_cannot_be_guessed_by_equal_amount():
    doc, facts = document()
    assert bind(doc, facts, parent='城乡社区服务事务')[0] is None


def test_two_independent_exact_leaf_chains_can_bind_under_wrong_parent_label(monkeypatch):
    from src.engine import native_final_disclosure as target
    from src.engine.native_narrative_checks import _Text
    doc,facts=document(extra=(('2120101','行政运行'),))
    doc.page_texts[0]+='\n“城乡社区支出(类)旧款名(款)其他城乡社区管理事务支出(项)”支出决算100万元。\n“城乡社区支出(类)旧款名(款)行政运行(项)”支出决算100万元。'
    text=_Text.from_pages(doc.page_texts);items=list(target._ITEM.finditer(text.compact))
    monkeypatch.setattr(target,'_general_ranges',lambda doc:(text,[('一般公共预算支出',0,len(text.compact))]))
    aliases=[];fact,reason=target._hierarchy_fact(doc,facts,items[0],siblings=items,aliases=aliases)
    assert reason is None and fact.subject_code=='2120199'
    proof=next(x for x in aliases if x.get('role')=='independent_exact_leaf_chain')
    assert proof['corroborating_leaf_codes']==['2120101','2120199']
    assert proof['narrative_parent']=='旧款名' and proof['table_parent']=='城乡社区管理\n事务'
    assert target._hierarchy_fact(doc,facts,items[0],siblings=items[:1])[0] is None
