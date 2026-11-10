"""原生经济分类续页首行是业务行，不能因默认至少一行表头而被丢掉。"""
from copy import deepcopy

import pytest

from src.services.pdf_parser import PDFParser
from tests.remediation.test_shiquan_native_empty_rows import grid_document


def fragments():
    previous=grid_document([
        ['经济分类科目编码','','科目名称','决算数','经济分类科目编码','','科目名称','决算数'],
        ['类','款','','','类','款','',''],
        ['301','','工资福利支出','','310','','资本性支出',''],
        ['301','01','基本工资','','310','13','公务用车购置',''],
    ]).parse_artifact.table_fragments[0]
    current=grid_document([
        ['','','','','310','19','其他交通工具购置',''],
        ['','','','','310','21','文物和陈列品购置',''],
    ]).parse_artifact.table_fragments[0]
    current.page_number=2;current.fragment_uid='continuation';current.table_code='UNKNOWN_P2_T0';current.title=''
    for table in [previous,current]:
        for cell in table.cells:
            cell.page_number=table.page_number
            if cell.raw_text:
                cell.native_text_sources=[dict(text=cell.raw_text,bbox=list(cell.bbox),
                    method='native_contiguous_character_flow',physical_page=table.page_number,
                    start_char_index=0,end_char_index=len(cell.raw_text))]
    return previous,current


def test_complete_native_coded_continuation_first_row_is_data_without_changing_amounts():
    previous,current=fragments();before=current.to_dict();parser=PDFParser(None)
    fixed=parser._recover_native_continuation_data_header(previous,current)
    assert current.to_dict()==before
    assert fixed.header_row_count==0 and all(not c.is_header for c in fixed.cells)
    for old,new in zip(current.cells,fixed.cells):
        assert (old.raw_text,old.numeric_value,old.bbox,old.native_blank_verified)==(
            new.raw_text,new.numeric_value,new.bbox,new.native_blank_verified)
    assert parser._native_continuation_columns_match(previous,fixed)
    assert parser._reconstruction_decisions[-1]['monetary_cells_unchanged']


def test_clipped_native_name_stays_literal_while_verified_code_proves_business_row_role():
    previous,current=fragments();name=next(c for c in current.cells if (c.row_idx,c.col_idx)==(0,6))
    name.native_text_sources=[];name.raw_text=current.rows[0][6]='其他交通工具购'
    fixed=PDFParser(None)._recover_native_continuation_data_header(previous,current)
    assert fixed.header_row_count==0
    assert next(c for c in fixed.cells if (c.row_idx,c.col_idx)==(0,6)).raw_text=='其他交通工具购'


@pytest.mark.parametrize('change',['no_reference','other_table','ocr','shifted_column','missing_literal',
    'other_class','reversed_code','different_page','actual_header','unit_conflict','duplicate_cell',
    'missing_name_cell','ocr_name_cell','neighboring_row_literal'])
def test_uncertain_or_actual_header_never_loses_header_role(change):
    previous,current=fragments();parser=PDFParser(None)
    code=next(c for c in current.cells if (c.row_idx,c.col_idx)==(0,4))
    if change=='no_reference':previous=None
    elif change=='other_table':previous.table_code='FIN_03_expenditure'
    elif change=='ocr':current.extraction_method='ocr'
    elif change=='shifted_column':
        for c in current.cells:
            if c.col_idx==4:c.bbox=(c.bbox[0]+3,c.bbox[1],c.bbox[2]+3,c.bbox[3])
    elif change=='missing_literal':code.native_text_sources=[]
    elif change=='other_class':current.rows[0][4]=code.raw_text='311'
    elif change=='reversed_code':current.rows[0][5]='11';next(c for c in current.cells if (c.row_idx,c.col_idx)==(0,5)).raw_text='11'
    elif change=='different_page':current.page_number=3
    elif change=='actual_header':current.rows[0]=['类','款','科目名称','决算数','类','款','科目名称','决算数']
    elif change=='unit_conflict':current.unit_hint='元'
    elif change=='duplicate_cell':current.cells.append(deepcopy(code))
    elif change=='missing_name_cell':current.cells=[c for c in current.cells if (c.row_idx,c.col_idx)!=(0,6)]
    elif change=='ocr_name_cell':next(c for c in current.cells if (c.row_idx,c.col_idx)==(0,6)).ocr_provenance={'engine':'ocr'}
    else:code.native_text_sources[0]['bbox'][1]+=25;code.native_text_sources[0]['bbox'][3]+=25
    before=current.to_dict();fixed=parser._recover_native_continuation_data_header(previous,current)
    assert fixed.to_dict()==before and fixed.header_row_count==1
