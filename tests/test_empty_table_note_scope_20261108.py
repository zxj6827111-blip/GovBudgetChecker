"""三张决算空表共用说明口径：短句有效，但不能跨表、跨章节借用。"""
from types import SimpleNamespace

import pytest

from src.engine.rules_v33 import R33114_EmptyTableStatementCheck, _empty_table_note_present


TABLES = [
    ('三公', '财政拨款“三公”经费支出决算表'),
    ('政府性基金', '政府性基金预算财政拨款收入支出决算表'),
    ('国有资本经营', '国有资本经营预算财政拨款收入支出决算表'),
]


@pytest.mark.parametrize('keyword,title', TABLES)
@pytest.mark.parametrize('note', ['注：本表无数据。', '本表无数据。', '说明：本表无数据。', '注：本 表 无 数 据。'])
def test_short_note_belongs_to_the_table_above(keyword, title, note):
    text = title + '\n单位：万元\n' + note
    assert _empty_table_note_present(text, [keyword])
    doc = SimpleNamespace(page_texts=['甲局2025年度决算', text],
                          page_tables=[[], [[['项目', '支出'], ['合计', '']]]])
    assert R33114_EmptyTableStatementCheck().apply(doc) == []


@pytest.mark.parametrize('keyword,title', TABLES)
def test_generic_table_description_does_not_disclose_empty_data(keyword, title):
    assert not _empty_table_note_present(title + '\n注：本表反映单位本年度收支情况。', [keyword])
    assert not _empty_table_note_present('注：本表无数据。\n' + title, [keyword])


def test_other_table_and_narrative_cannot_supply_the_note():
    sg, fund = TABLES[0][1], TABLES[1][1]
    assert not _empty_table_note_present(sg + '\n' + fund + '\n注：本表无数据。', ['三公'])
    assert not _empty_table_note_present(fund + '\n注：本表无数据。\n' + sg, ['三公'])
    assert not _empty_table_note_present(sg + '\n注：无政府性基金支出，故本表无数据。', ['三公'])
    assert not _empty_table_note_present(
        sg + '\n七、财政拨款“三公”经费支出决算情况说明\n注：本表无数据。', ['三公'])
    assert not _empty_table_note_present('本表无数据。', ['三公'])


def test_nonzero_sparse_table_is_not_reported_empty():
    # GS-2022-08 的最后一次补丁必须有正反例保护：单行 32.00 不是空表。
    text = TABLES[0][1] + '\n单位：万元\n'
    def doc(value):
        return SimpleNamespace(page_texts=['甲局2022年度决算', text],
                               page_tables=[[], [[['预算数', '决算数'], ['', value]]]])
    rule = R33114_EmptyTableStatementCheck()
    assert rule.apply(doc('32.00')) == []
    assert len(rule.apply(doc(''))) == 1


def test_nonzero_san_gong_and_empty_fund_on_the_same_page_are_independent():
    from src.engine.rules_v33 import R33109_EmptyTables_Statement, build_document

    sg, fund = TABLES[0][1], TABLES[1][1]
    text = sg + '\n注：本表反映单位本年度三公经费支出预决算情况。\n' + fund
    text += '\n说明：本单位没有政府性基金预算财政拨款收入和支出，故本表无数据。'
    nonzero = [['财政拨款三公经费', '合计'], ['预算数', '决算数'], ['32.00', '32.00']]
    empty = [['项目', '本年收入', '本年支出'], ['合计', '', '']]
    doc = build_document('same-page.pdf', ['甲局2022年度决算', text], [[], [nonzero, empty]], 0)
    assert R33114_EmptyTableStatementCheck().apply(doc) == []
    assert R33109_EmptyTables_Statement().apply(doc) == []
    doc.page_texts[1] = sg + '\n' + fund + '\n注：本表无数据。'
    doc.page_tables[1][0] = [['预算数', '决算数'], ['', '']]
    issues = R33114_EmptyTableStatementCheck().apply(doc)
    assert len(issues) == 1 and '三公' in issues[0].message
