"""跨横线折行只能按连续原字符流恢复，不能猜码或改写金额。"""
from types import SimpleNamespace

from src.schemas.parse_artifact import ParsedCell, ParsedTable
from src.services.native_dimension_recovery import repair_native_dimensions, repaired_native_grid_shadow
from src.services.pdf_parser import PDFParser


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
