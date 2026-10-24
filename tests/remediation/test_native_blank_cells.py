"""原件空白参与加法的边界：漏字、图像、缺格和冲突不能自动通过。"""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.engine.fact_context import build_fact_context_from_artifact
from src.engine.native_empty_operands import blank_term, valid_blank_observation
from src.engine.pipeline import build_document
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import R33241_Table3_ExpenseAdvancedCheck, R33244_Table7_ThreePublicAdvancedCheck
from src.engine.common_rules import CMM001_ThreePublicNarrativeConsistency
from src.schemas.parse_artifact import CheckReceipt, LogicalTableInstance, ParseArtifact, ParsedCell, ParsedTable
from src.services.pdf_parser import PDFParser
from src.services.rule_source_binding import bind_rule_sources


T3 = 'FIN_03_expenditure'


def grid_document(rows, *, code=T3, headers=2, text='甲局2024年度部门决算'):
    cells = []
    parser = PDFParser(None)
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            raw = str(value or '')
            cells.append(ParsedCell(r, c, raw, raw, parser._parse_numeric(raw), 1,
                                    (c*70, r*25+50, (c+1)*70, r*25+75), r<headers,
                                    .99, '万元', 'lines', native_blank_verified=not raw))
    if code == 'FIN_07_three_public':
        # 用真实合并跨度表示三层表头，避免把测试中的空占位格当作独立表头。
        spans = {(0,0):12,(1,0):2,(1,2):2,(1,4):6,(1,10):2,(2,4):2,(2,6):2,(2,8):2}
        for cell in cells:
            if cell.row_idx < 3:
                if not cell.raw_text:
                    cell.bbox = None
                elif (cell.row_idx,cell.col_idx) in spans:
                    cell.bbox=(cell.col_idx*70,cell.row_idx*25+50,
                               (cell.col_idx+spans[cell.row_idx,cell.col_idx])*70,cell.row_idx*25+75)
    table = ParsedTable(code, '支出决算表' if code==T3 else '财政拨款“三公”经费支出决算表',
                        1, 0, (0,50,len(rows[0])*70,len(rows)*25+50), headers,.99,'lines','万元',rows,cells,'fragment')
    instance = LogicalTableInstance('instance', code, table.title,['fragment'],1,1,
                                    org_name='甲局',fiscal_year=2024)
    artifact = ParseArtifact(source_file_hash='a'*64, parse_run_uid='run', table_fragments=[table],
                             physical_fragments=[table],logical_tables=[instance])
    context = build_fact_context_from_artifact(artifact,org_name='甲局',fiscal_year=2024,
                                               report_kind='final',doc_type='dept_final')
    doc = build_document('native.pdf',[text],[[rows]],0)
    doc.parse_artifact,doc.fact_context=artifact,context
    return doc


def expenditure(total='10.00'):
    return grid_document([
        ['项目',None,'本年支出合计','基本支出','项目支出','上缴上级支出','经营支出','对附属单位补助支出'],
        ['功能分类科目编码','科目名称',None,None,None,None,None,None],
        ['合计',None,total,'7.00','3.00','','',''],
        ['201','一般公共服务支出',total,'7.00','3.00','','',''],
    ])


def test_native_blank_arithmetic_preserves_original_facts_and_receipt_sources():
    doc=expenditure()
    before=doc.fact_context.to_dict()
    assert R33241_Table3_ExpenseAdvancedCheck().apply(doc)==[]
    assert doc.fact_context.to_dict()==before
    assert all(f.raw_value for f in doc.fact_context.facts)
    receipt=doc.fact_receipts[0]
    assert receipt['status']=='pass'
    assert len(receipt['cell_observations'])==3
    assert CheckReceipt.from_dict(receipt).to_dict()['cell_observations']==receipt['cell_observations']
    assert all(valid_blank_observation(doc.parse_artifact,doc.fact_context,o) for o in receipt['cell_observations'])


@pytest.mark.parametrize('problem',['missing_cell','uncertified','image_source','text_strategy','stale_version',
                                   'wrong_unit','cell_unit_conflict','foreign_table','numeric_fact_missing','duplicate_fact'])
def test_unknown_or_conflicting_operands_remain_uncompleted(problem):
    doc=expenditure()
    table=doc.parse_artifact.table_fragments[0]
    blank=next(c for c in table.cells if c.row_idx==2 and c.col_idx==5)
    if problem=='missing_cell': table.cells.remove(blank)
    elif problem=='uncertified': blank.native_blank_verified=False
    elif problem=='image_source': blank.extraction_method='ocr'
    elif problem=='text_strategy': table.extraction_method='text'
    elif problem=='stale_version': doc.parse_artifact.parse_run_uid='old'
    elif problem=='wrong_unit': table.unit_hint=None
    elif problem=='cell_unit_conflict': blank.unit_hint='元'
    elif problem=='foreign_table': doc.parse_artifact.logical_tables[0].org_name='乙局'
    elif problem=='numeric_fact_missing':
        doc.fact_context.facts=[f for f in doc.fact_context.facts if not (f.subject_code=='total' and f.indicator=='project_actual')]
    else:
        duplicate=deepcopy(doc.fact_context.facts[0])
        duplicate.fact_uid='duplicate'
        doc.fact_context.facts.append(duplicate)
    with pytest.raises(RuleDeferred): R33241_Table3_ExpenseAdvancedCheck().apply(doc)


def test_real_difference_survives_blank_support_and_support_must_be_current():
    doc=expenditure('20.00')
    issues=R33241_Table3_ExpenseAdvancedCheck().apply(doc)
    assert any(i.severity=='error' and '10.00' in i.message for i in issues)
    issue=next(i for i in issues if i.severity=='error')
    assert len(issue.location['native_blank_sources'])==3
    finding={'id':'rule-test','source':'rule','rule_id':'V33-241','location':deepcopy(issue.location),'evidence':[]}
    pages=['甲局2024年度部门决算 20.00 7.00 3.00']
    result={'rule_findings':[finding]}
    args=dict(page_texts=pages,source_file_hash=doc.fact_context.file_hash,artifact=doc.parse_artifact,
              fact_context=doc.fact_context,consumption=doc.structured_consumption)
    assert bind_rule_sources(result,**args)['bound_rule_findings']==1
    finding['location']['native_blank_sources'][0]['bbox'][0]+=1
    assert bind_rule_sources(result,**args)['bound_rule_findings']==0


def test_blank_total_is_not_backfilled_from_nonzero_parts():
    doc=expenditure('')
    with pytest.raises(RuleDeferred): R33241_Table3_ExpenseAdvancedCheck().apply(doc)


@pytest.mark.parametrize('obstacle',['none','glyph','image','unknown_coords','merged_placeholder','no_text_layer'])
def test_parser_certifies_only_native_grid_cells_with_no_glyph_or_image(obstacle):
    cell=ParsedCell(0,0,'','',None,1,(0,0,30,30),True,.99,'万元','lines')
    page=SimpleNamespace(chars=[{'x0':40,'top':40,'x1':50,'bottom':50}],images=[])
    if obstacle=='glyph': page.chars.append({'x0':2,'top':2,'x1':12,'bottom':12})
    elif obstacle=='image': page.images.append({'x0':0,'top':0,'x1':30,'bottom':30})
    elif obstacle=='unknown_coords': page.images.append({})
    elif obstacle=='merged_placeholder': cell.bbox=None
    elif obstacle=='no_text_layer': page.chars=[]
    PDFParser._certify_native_blank_cells(page,[cell],'lines')
    assert cell.native_blank_verified==(obstacle=='none')
    assert ParsedCell.from_dict(cell.to_dict()).native_blank_verified==cell.native_blank_verified
    assert cell.raw_text=='' and cell.numeric_value is None and cell.normalized_value_wan is None


@pytest.mark.parametrize('glyph', [' ', '\t', '\u3000', '\u00a0'])
def test_layout_whitespace_in_an_observed_empty_grid_is_not_an_amount(glyph):
    cell = ParsedCell(0, 0, '', '', None, 1, (0, 0, 30, 30), False, .99, '万元', 'lines')
    page = SimpleNamespace(chars=[{'text': glyph, 'x0': 2, 'top': 2, 'x1': 12, 'bottom': 12}], images=[])
    PDFParser._certify_native_blank_cells(page, [cell], 'lines')
    assert cell.native_blank_verified
    assert cell.numeric_value is None and cell.normalized_value_wan is None


@pytest.mark.parametrize('glyph', ['0', '—', '待', '\u200b'])
def test_visible_or_unknown_glyphs_are_not_certified_as_native_blanks(glyph):
    cell = ParsedCell(0, 0, '', '', None, 1, (0, 0, 30, 30), False, .99, '万元', 'lines')
    page = SimpleNamespace(chars=[{'text': glyph, 'x0': 2, 'top': 2, 'x1': 12, 'bottom': 12}], images=[])
    PDFParser._certify_native_blank_cells(page, [cell], 'lines')
    assert not cell.native_blank_verified


def three_public(purchase='', absence='无公务用车购置支出。'):
    rows=[['财政拨款“三公”经费']+[None]*11,
          ['合计',None,'因公出国（境）费',None,'公务用车购置及运行维护费']+[None]*5+['公务接待费',None],
          [None]*4+['小计',None,'公务用车购置费',None,'公务用车运行维护费',None,None,None],
          ['预算数','决算数']*6,
          ['10.00','10.00','2.00','2.00','5.00','5.00',purchase,purchase,'5.00','5.00','3.00','3.00']]
    text=('甲局2024年度部门决算\n七、财政拨款“三公”经费支出决算情况说明\n'
          '“三公”经费年初预算为10.00万元，支出决算为10.00万元。\n（二）具体情况说明\n'
          '因公出国（境）费支出2.00万元。公务用车购置及运行维护费支出5.00万元。'
          +absence+'公务用车运行维护费支出5.00万元。公务接待费支出3.00万元。')
    return grid_document(rows,code='FIN_07_three_public',headers=4,text=text)


def test_explicit_no_purchase_and_native_blank_complete_without_made_up_money():
    doc=three_public()
    assert R33244_Table7_ThreePublicAdvancedCheck().apply(doc)==[]
    assert CMM001_ThreePublicNarrativeConsistency().apply(doc)==[]
    assert any(r['status']=='not_applicable' and r['cell_observations'] for r in doc.fact_receipts)
    assert any('无公务用车购置支出' in ref['span']
               for r in doc.fact_receipts for ref in r['disclosure_sources'])
    assert not any(f.indicator=='vehicle_purchase' for f in doc.fact_context.facts)


@pytest.mark.parametrize('statement',['公务用车购置情况待补充。','2023年度无公务用车购置支出。','乙局无公务用车购置支出。'])
def test_no_purchase_is_not_guessed_from_missing_or_foreign_statement(statement):
    with pytest.raises(RuleDeferred): CMM001_ThreePublicNarrativeConsistency().apply(three_public(absence=statement))


def test_no_purchase_statement_does_not_hide_actual_nonzero_purchase():
    doc=three_public(purchase='1.00')
    issues=CMM001_ThreePublicNarrativeConsistency().apply(doc)
    assert any('购置' in i.message and i.severity!='info' for i in issues)
