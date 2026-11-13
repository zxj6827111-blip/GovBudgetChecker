from copy import deepcopy

import pytest

from src.engine.native_grid_roles import merged_note_row, labels_covered_by_native_cells
from src.engine.fact_context import FactContext
from src.engine.native_table_arithmetic import _economic_table_view, _economic_zero_layout_tail
from tests.remediation.test_shiquan_native_empty_rows import grid_document, cell


def economic_note():
    doc=grid_document([
        ['经济分类科目编码','科目名称','决算数','经济分类科目编码','科目名称','决算数'],
        ['301','工资福利支出','10.00','302','商品和服务支出','3.00'],
        ['人员经费合计','','10.00','公用经费合计','','3.00'],
        ['注：小数点保留两位。',None,None,None,None,None]])
    table=doc.parse_artifact.table_fragments[0]
    note=cell(doc,3,0)
    note.col_span=6
    note.bbox=(0,125,420,150)
    for col in range(1,6):cell(doc,3,col).bbox=None
    doc.parse_artifact.physical_fragments=[deepcopy(table)]
    return doc,table


def test_full_width_native_note_is_not_a_subject_row():
    doc,table=economic_note()
    assert merged_note_row(table,3)
    assert _economic_table_view(doc,[f for f in doc.fact_context.facts if f.table_code==table.table_code])[1] is None


@pytest.mark.parametrize('change',['not_merged','another_real_cell','numeric_cell','ocr','ordinary_label'])
def test_note_text_does_not_hide_real_or_unverified_cells(change):
    doc,table=economic_note()
    note=cell(doc,3,0)
    if change=='not_merged':note.col_span=1
    elif change=='another_real_cell':cell(doc,3,5).bbox=(350,125,420,150)
    elif change=='numeric_cell':
        cell(doc,3,5).raw_text='3.00'
        cell(doc,3,5).numeric_value=3
    elif change=='ocr':note.ocr_provenance={'model':'ocr'}
    else:note.raw_text='工资福利支出'
    assert not merged_note_row(table,3)


def test_summary_label_merge_covers_labels_but_never_missing_money_cell():
    doc,table=economic_note()
    label=cell(doc,2,0)
    label.col_span=2
    table.cells.remove(cell(doc,2,1))
    doc.parse_artifact.physical_fragments=[deepcopy(table)]
    assert labels_covered_by_native_cells(table,2,[0,1])
    assert not labels_covered_by_native_cells(table,2,[0,1,2,6])
    assert _economic_table_view(doc,[f for f in doc.fact_context.facts if f.table_code==table.table_code])[1] is None
    table.cells.remove(cell(doc,2,2))
    assert _economic_table_view(doc,doc.fact_context.facts)[1] is not None


def tail():
    doc=grid_document([
        ['经济分类科目编码','科目名称','决算数','经济分类科目编码','科目名称','决算数'],
        ['','','','31007','信息网络及软件购',''],
        ['','','','','置更新','']])
    table=doc.parse_artifact.table_fragments[0]
    for col in range(6):
        c=cell(doc,2,col)
        c.page_number=2
        c.origin_row_idx=0
        c.origin_fragment_uid='next-page'
    doc.parse_artifact.physical_fragments=[deepcopy(table)]
    doc.fact_context=FactContext([],doc.parse_artifact.source_file_hash,doc.parse_artifact.parse_run_uid,
                                org_name='甲局',fiscal_year=2025,report_kind='final')
    cells={(c.row_idx,c.col_idx):c for c in table.cells}
    return doc,table,cells,{'source_label_cols':[3,4],'source_col_idx':5}


def test_certified_zero_name_tail_cannot_block_numerical_sum():
    doc,table,cells,mapping=tail()
    assert _economic_zero_layout_tail(doc,table,cells,2,mapping)['value']=='0'


@pytest.mark.parametrize('change',['same_page','not_first_row','value','dash','unverified','unknown_previous','foreign_run','missing_physical','off'])
def test_name_tail_requires_entire_physical_zero_proof(change,monkeypatch):
    doc,table,cells,mapping=tail()
    if change=='same_page':cells[2,4].page_number=1
    elif change=='not_first_row':cells[2,4].origin_row_idx=2
    elif change=='value':
        cells[2,5].raw_text='1.00'
        cells[2,5].numeric_value=1
    elif change=='dash':cells[2,5].raw_text='—'
    elif change=='unverified':cells[2,5].native_blank_verified=False
    elif change=='unknown_previous':cells[1,3].raw_text='未知'
    elif change=='foreign_run':doc.parse_artifact.parse_run_uid='other'
    elif change=='missing_physical':doc.parse_artifact.physical_fragments=[]
    else:monkeypatch.setenv('GOVBUDGET_EMPTY_VALUE_CONVENTIONS','off')
    assert _economic_zero_layout_tail(doc,table,cells,2,mapping) is None


# ---------------------------------------------------------------------------
# D0 RC-2c：同科目重复原行（原件排版重复，如党群服务中心 31099 连续两行
# 0.00）按同一业务行归集，不得阻断整个明细归集；内容不一致仍 fail-closed。
# ---------------------------------------------------------------------------

def duplicate_row_doc():
    return grid_document([
        ['经济分类科目编码', '科目名称', '决算数', '经济分类科目编码', '科目名称', '决算数'],
        ['301', '工资福利支出', '100.00', '302', '商品和服务支出', '30.00'],
        ['30101', '基本工资', '60.00', '30201', '办公费', '10.00'],
        ['30102', '津贴补贴', '40.00', '30202', '印刷费', '20.00'],
        ['', '', '', '30299', '其他商品和服务支出', '0.00'],
        ['', '', '', '30299', '其他商品和服务支出', '0.00'],
        ['', '', '', '310', '资本性支出', ''],
        ['人员经费合计', '', '100.00', '公用经费合计', '', '30.00'],
    ])


def test_identical_duplicate_row_dedupes_instead_of_blocking():
    doc = duplicate_row_doc()
    facts = [f for f in doc.fact_context.facts if f.table_code == 'FIN_06_basic_expenditure']
    view, reason = _economic_table_view(doc, facts)
    assert reason is None, f"同值重复行不得阻断归集: {reason}"
    assert getattr(doc, '_economic_duplicate_row_observations', []), "重复行应记观察清单"
    obs = doc._economic_duplicate_row_observations[0]
    assert obs['subject_code'] == '30299' and obs['row_idx'] == 5


def test_conflicting_duplicate_rows_stay_fail_closed():
    doc = duplicate_row_doc()
    table = doc.parse_artifact.table_fragments[0]
    # 把第二个 30299 行的金额改成不同值 → 行身份不可判，整表未决
    conflict = next(c for c in table.cells if (c.row_idx, c.col_idx) == (5, 5))
    conflict.raw_text = '9.00'
    conflict.numeric_value = 9
    doc.parse_artifact.physical_fragments = [deepcopy(table)]
    facts = [f for f in doc.fact_context.facts if f.table_code == 'FIN_06_basic_expenditure']
    view, reason = _economic_table_view(doc, facts)
    assert view is None and '重复原行身份' in reason


def test_duplicate_row_delivered_as_low_finding_by_v33243():
    """D0 复核（用户裁定）：重复原行缺陷须交付 low finding，不得只留回执。"""
    from src.engine.rules_v33 import R33243_Table6_BasicExpenseAdvancedCheck
    from src.engine.native_table_arithmetic import check_basic_economics

    doc = duplicate_row_doc()
    rule = R33243_Table6_BasicExpenseAdvancedCheck()
    issues = check_basic_economics(rule, doc)
    dup_hits = [i for i in issues if '重复科目行' in str(i.message)]
    assert dup_hits, "重复行缺陷应交付 low finding"
    assert dup_hits[0].severity == 'low'
    assert '30299' in str(dup_hits[0].message)
    assert (dup_hits[0].location or {}).get('page'), "重复行 finding 必须带页码"
