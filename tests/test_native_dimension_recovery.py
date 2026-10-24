"""跨横线折行只能按连续原字符流恢复，不能猜码或改写金额。"""
from types import SimpleNamespace

from src.schemas.parse_artifact import ParsedCell, ParsedTable
from src.services.native_dimension_recovery import repair_native_dimensions, repaired_native_grid_shadow
from src.services.native_dimension_recovery import repair_native_code_baselines
from src.services.native_dimension_recovery import repair_native_cross_page_name
from src.services.pdf_parser import PDFParser
from src.services.fiscal_column_identity import canonical_column_mappings
from src.services.fiscal_table_rules import NINE_TABLE_RULES


def table(rows, code='FIN_06_basic_expenditure', headers=2):
    parser = PDFParser(None)
    cells = []
    for r,row in enumerate(rows):
        for c,value in enumerate(row):
            raw = str(value or '')
            cells.append(ParsedCell(r,c,raw,raw,parser._parse_numeric(raw),1,
                        (c*40,r*15,(c+1)*40,(r+1)*15),r<headers,.99,'万元','lines'))
    return ParsedTable(code,'表',1,0,(0,0,len(rows[0])*40,len(rows)*15),headers,.99,'lines','万元',rows,cells,'grid')


def chars(text, x, y):
    return [{'text':s,'x0':x+i*4,'x1':x+i*4+3,'top':y,'bottom':y+8} for i,s in enumerate(text)]


def economic():
    return table([['经济分类科目编码',None,'科目名称','决算数','经济分类科目编码',None,'科目名称','决算数'],
                  ['类','款',None,None,'类','款',None,None],
                  ['30','','工资福利支出','100.00','302','','商品和服务支出','50.00'],
                  ['1\n30','01','基本工资','100.00','302','01','办公费','50.00']])


def test_wrapped_economic_code_recovered_from_source_flow_without_amount_changes():
    grid=economic()
    native=chars('30',2,32)+chars('1',4,44)+chars('100',122,32)+chars('30',2,47)+chars('1',4,59)
    fixed=repair_native_dimensions(SimpleNamespace(chars=native),grid)
    assert fixed.rows[2][0]==fixed.rows[3][0]=='301'
    assert fixed.rows[2][3]==fixed.rows[3][3]=='100.00'
    assert fixed.fragment_uid==grid.fragment_uid
    source=next(c for c in fixed.cells if c.row_idx==2 and c.col_idx==0)
    assert source.grid_raw_text=='30' and source.native_text_sources[0]['text']=='301'
    assert source.native_text_sources[0]['end_char_index']==3
    assert ParsedCell.from_dict(source.to_dict()).native_text_sources==source.native_text_sources


def test_noncontiguous_characters_are_not_guessed_into_a_complete_code():
    grid=economic()
    native=chars('30',2,32)+chars('别',82,32)+chars('1',4,44)
    assert repair_native_dimensions(SimpleNamespace(chars=native),grid) is grid


def test_summary_label_not_polluted_by_previous_rows_wrapped_tail():
    grid=table([['项目','决算数','项目','合计','一般公共预算财政拨款','政府性基金预算财政拨款','国有资本经营预算财政拨款'],
                ['本年收入合计','100','其他支','90','90','0','0'],
                ['总计','100','出\n本年支出合计','90','90','0','0']],code='UNKNOWN_P18_T0',headers=1)
    native=chars('其他支',82,17)+chars('出',82,29)+chars('90',122,17)+chars('本年支出合计',82,32)
    fixed=repair_native_dimensions(SimpleNamespace(chars=native),grid)
    assert fixed.rows[1][2]=='其他支出'
    assert fixed.rows[2][2]=='本年支出合计'
    assert fixed.rows[2][3:]==grid.rows[2][3:]


def test_normal_cells_and_non_native_tables_are_untouched():
    grid=economic()
    assert repair_native_dimensions(SimpleNamespace(chars=chars('30',2,32)),grid) is grid
    grid.extraction_method='ocr'
    native=chars('30',2,32)+chars('1',4,44)
    assert repair_native_dimensions(SimpleNamespace(chars=native),grid) is grid


def test_money_columns_are_not_reconstructed_even_when_digits_spill():
    grid=economic()
    native=chars('100',122,32)+chars('0',122,44)
    assert repair_native_dimensions(SimpleNamespace(chars=native),grid) is grid


def test_one_lane_continuation_recovers_code_and_preserves_merged_footer_label():
    grid=table([['','','','','31','99','其他资本性支出','15.90'],
                ['人员经费合计','','','100.00','0\n公用经费合计','','','65.32']],
               code='UNKNOWN_P2_T0',headers=1)
    footer=next(c for c in grid.cells if c.row_idx==1 and c.col_idx==4)
    footer.bbox=(160,15,280,30)
    native=chars('31',162,2)+chars('0',164,14)+chars('99',202,2)+chars('其他资本性支出',242,2)
    native+=chars('15.90',282,2)+chars('公用经费合计',200,17)
    fixed=repair_native_dimensions(SimpleNamespace(chars=native),grid)
    assert fixed.rows[0][4]=='310'
    assert fixed.rows[1][4]=='公用经费合计'
    assert fixed.rows[0][7]=='15.90' and fixed.rows[1][7]=='65.32'
    shadow=table([['其他资本性支出','15.90'],['公用经费合计','65.32']],code='UNKNOWN_P2_T1')
    shadow.extraction_method='text'
    for c in shadow.cells:
        c.bbox=(240 if c.col_idx==0 else 280,c.row_idx*15,279 if c.col_idx==0 else 320,(c.row_idx+1)*15)
    assert repaired_native_grid_shadow(fixed,shadow)
    shadow.cells[1].raw_text='15.91'
    assert not repaired_native_grid_shadow(fixed,shadow)


def complete_economic(code='01\n30202'):
    return table([['经济分类科目编码', '科目名称', '决算数', '经济分类科目编码', '科目名称', '决算数'],
                  ['301', '工资福利支出', '100.00', '302', '商品和服务支出', '50.00'],
                  ['30101', '基本工资', '100.00', code, '印刷费', '50.00']], headers=1)


def test_baseline_recovers_only_the_literal_full_code_aligned_with_its_amount():
    grid = complete_economic()
    native = chars('01', 122, 26) + chars('30202', 122, 32) + chars('印刷费', 162, 32) + chars('50.00', 202, 32)
    fixed = repair_native_code_baselines(SimpleNamespace(chars=native), grid)
    assert fixed.rows[2][3] == '30202'
    source = next(c for c in fixed.cells if (c.row_idx, c.col_idx) == (2, 3))
    assert source.grid_raw_text == '01\n30202'
    proof = source.native_text_sources[0]
    assert ''.join(c['text'] for c in native[proof['start_char_index']:proof['end_char_index']]) == '30202'
    assert [(c.raw_text, c.bbox) for c in fixed.cells if c.col_idx in {2, 5}] == [
        (c.raw_text, c.bbox) for c in grid.cells if c.col_idx in {2, 5}]


def test_native_malformed_code_is_preserved_instead_of_trimmed_to_a_dictionary_match():
    grid = complete_economic('302041')
    native = chars('302041', 122, 32) + chars('50.00', 202, 32)
    assert repair_native_code_baselines(SimpleNamespace(chars=native), grid) is grid


def test_baseline_does_not_join_noncontiguous_source_characters():
    grid = complete_economic()
    native = chars('302', 122, 32) + chars('别', 82, 32) + chars('02', 134, 32) + chars('50.00', 202, 32)
    assert repair_native_code_baselines(SimpleNamespace(chars=native), grid) is grid


def test_merged_continuation_footer_keeps_literal_money_rectangles():
    parser = PDFParser(None)
    previous = table([['科目编码', '科目名称', '合计', '基本支出', '项目支出'],
                      ['2299999', '其他支出', '.04', '.00', '.04']],
                     code='FIN_05_general_public_expenditure', headers=1)
    footer = table([['合计', '100.04', '60.00', '40.04']], code='UNKNOWN_P2_T0', headers=1)
    footer.page_number = 2
    for cell in footer.cells:
        cell.page_number = 2
        cell.bbox = ((0, 0, 80, 15) if not cell.col_idx else
                     ((cell.col_idx+1)*40, 0, (cell.col_idx+2)*40, 15))
    fixed = parser._align_native_continuation_footer(previous, footer)
    assert fixed.rows == [['合计', '', '100.04', '60.00', '40.04']]
    assert fixed.header_row_count == 0
    assert [(c.raw_text, c.bbox) for c in fixed.cells if c.numeric_value is not None] == [
        (c.raw_text, c.bbox) for c in footer.cells if c.numeric_value is not None]
    assert next(c for c in fixed.cells if c.col_idx == 1).bbox is None
    footer.cells[1].bbox = (81.5, 0, 120, 15)
    assert parser._align_native_continuation_footer(previous, footer) is footer


def page_broken_name():
    previous = table([['科目编码', '科目名称', '合计', '基本支出', '项目支出'],
                      ['2080505', '机关事业单位\n基本养老保险', '10.00', '10.00', '']],
                     code='FIN_05_general_public_expenditure', headers=1)
    for cell in previous.cells:
        cell.bbox = (cell.bbox[0], cell.bbox[1] + 150, cell.bbox[2], cell.bbox[3] + 150)
    previous.bbox = (0, 150, 200, 180)
    current = table([['2080506', '职业年金缴费支出', '5.00', '5.00', '']], code='UNKNOWN_P2_T0', headers=0)
    current.page_number = 2
    current.bbox = (0, 30, 200, 45)
    for cell in current.cells:
        cell.page_number = 2
        cell.bbox = (cell.bbox[0], 30, cell.bbox[2], 45)
    previous_page = SimpleNamespace(height=200, chars=chars('机关事业单位', 42, 166) + chars('基本养老保险', 42, 173))
    page = SimpleNamespace(chars=chars('缴费支出', 42, 20))
    return previous_page, page, previous, current


def test_cross_page_tail_recovers_name_with_two_literal_source_ranges():
    previous_page, page, previous, current = page_broken_name()
    result = repair_native_cross_page_name(previous_page, page, previous, current, [previous])
    fixed = result[0]
    assert fixed.rows[1][1] == '机关事业单位基本养老保险缴费支出'
    cell = next(c for c in fixed.cells if (c.row_idx, c.col_idx) == (1, 1))
    assert cell.grid_raw_text == '机关事业单位\n基本养老保险'
    assert [s['physical_page'] for s in cell.native_text_sources] == [1, 2]
    assert fixed.rows[1][2:] == previous.rows[1][2:]
    for source, native in zip(cell.native_text_sources, (previous_page.chars, page.chars), strict=True):
        assert source['text'] == ''.join(c['text'] for c in native[source['start_char_index']:source['end_char_index']])


def test_cross_page_tail_already_in_an_empty_top_grid_row_keeps_its_original_source():
    prior_page, page, previous, _ = page_broken_name()
    current = table([['', '缴费支出', '', '', ''],
                     ['2080506', '职业年金缴费支出', '5.00', '5.00', '']], code='UNKNOWN_P2_T0', headers=1)
    current.page_number = 2
    for cell in current.cells:
        cell.page_number = 2
    page.chars = chars('缴费支出', 42, 2)
    fixed = repair_native_cross_page_name(prior_page, page, previous, current, [previous])[0]
    assert fixed.rows[1][1] == '机关事业单位基本养老保险缴费支出'
    assert current.rows[0][1] == '缴费支出'


def test_cross_page_tail_never_absorbs_other_columns_nonadjacent_pages_or_headers():
    for conflict in ('amount', 'code', 'header', 'gap', 'grid', 'noncontiguous', 'complete_name'):
        prior_page, page, previous, current = page_broken_name()
        if conflict == 'amount': page.chars += chars('10.00', 82, 20)
        elif conflict == 'code': page.chars += chars('2080505', 2, 20)
        elif conflict == 'header': page.chars = chars('科目名称', 42, 20)
        elif conflict == 'gap': current.page_number = 3
        elif conflict == 'grid': current.cells[1].bbox = (43, 30, 83, 45)
        elif conflict == 'noncontiguous': page.chars = chars('缴费', 42, 20) + chars('别', 82, 100) + chars('支出', 50, 20)
        else: previous.cells[6].raw_text = '机关事业单位养老支出'
        assert repair_native_cross_page_name(prior_page, page, previous, current, [previous])[0] is previous


def mixed_fund_header():
    grid = table([['项目', '', '年初结转和结余', '本年收入', '本年支出', '', '', '年末结转和结余'],
                  ['功能分类科目编码', '科目名称', '', '', '', '', '', ''],
                  ['合计', '', '0.00', '100.00', '合计', '基本支出', '项目支出', '0.00'],
                  ['229', '其他支出', '0.00', '100.00', '100.00', '30.00', '70.00', '0.00']],
                 code='FIN_08_gov_fund', headers=2)
    parent = next(c for c in grid.cells if (c.row_idx, c.col_idx) == (0, 4))
    parent.bbox = (160, 0, 280, 15)
    for cell in grid.cells:
        if cell.row_idx == 0 and cell.col_idx in {5, 6}: cell.bbox = None
    return grid


def test_mixed_header_and_income_total_row_preserves_all_literal_measure_columns():
    grid = mixed_fund_header()
    mappings = canonical_column_mappings(grid, NINE_TABLE_RULES[grid.table_code])
    by_col = {item['source_col_idx']: item['canonical_measure'] for item in mappings}
    assert {3: 'income_actual', 4: 'total_actual', 5: 'basic_actual', 6: 'project_actual'}.items() <= by_col.items()
    assert not next(c for c in grid.cells if (c.row_idx, c.col_idx) == (2, 3)).is_header
    assert grid.rows[2][3] == '100.00'
    assert grid.rows[2][4] == '合计'  # 原件没有的支出合计不能伪造。


def test_mixed_leaf_headers_require_a_complete_literal_parent_and_three_distinct_leaves():
    grid = mixed_fund_header()
    grid.rows[2][6] = '其他支出'
    assert not any(m['canonical_measure'] == 'total_actual'
                   for m in canonical_column_mappings(grid, NINE_TABLE_RULES[grid.table_code]))
    grid = mixed_fund_header()
    grid.cells[4].bbox = (160, 0, 200, 15)
    assert not any(m['canonical_measure'] == 'total_actual'
                   for m in canonical_column_mappings(grid, NINE_TABLE_RULES[grid.table_code]))
