"""独立审核 R1–R4 的永久回归：来源、槽位、OCR 绑定及结构实例。"""
import asyncio,copy
from dataclasses import asdict
from unittest.mock import AsyncMock
import pytest
from src.services import annual_templates,material_writing
from src.services.document_profile_resolver import resolve_document_profile
from src.services.template_scope import resolve_material_scope
from src.services.template_anchor_slots import instantiate_anchor,validated_slots
from src.services.template_anchor_checks import additional_anchor_issues
from src.services.ocr_qualification import bound_ocr_block
from src.engine.checklist_text_rules import CMM008_TemplateResidue
from src.schemas.parse_artifact import ParseArtifact,ParsedTextBlock,ParsedTable,LogicalTableInstance
from tests.test_material_writing_rules import document
from tests.test_template_anchor_checks import fixture_resolution
from tests.annual_template_p1_helpers import bound_identity_artifact

PAGES=['上海市示例局2024年度决算','2024年度决算表\n说明：本表反映部门本年度收入。','2024年度决算情况说明']


@pytest.mark.parametrize('entry',['offline','database','frozen','standalone'])
@pytest.mark.parametrize('filename',['示例局2024年度决算.pdf',None])
def test_r1_equal_filename_and_cover_use_original_year(monkeypatch,entry,filename):
    monkeypatch.setenv('GBC_TEMPLATE_SCOPE','true');monkeypatch.setenv('GBC_TEMPLATE_EXPORT','test-only')
    monkeypatch.delenv('DATABASE_URL',raising=False)
    snapshot={'available':True,'capability_scope':{'writing_auto':True},'target_year':2024}
    selection=[]
    def offline(path,digest,profile,pages):selection.append(profile);return snapshot
    monkeypatch.setattr(annual_templates,'offline_for_profile',offline)
    profile=resolve_document_profile(filename=filename,page_texts=PAGES);before=profile.to_dict()
    if entry in ('database','frozen'):
        from src.db.connection import DatabaseConnection
        conn=type('Connection',(),{'fetchrow':AsyncMock(return_value=None)})()
        monkeypatch.setenv('DATABASE_URL','test-only-no-network')
        monkeypatch.setattr(DatabaseConnection,'acquire',AsyncMock(return_value=conn));monkeypatch.setattr(DatabaseConnection,'release',AsyncMock())
        async def selected(*args):selection.append(args[-2]);return snapshot
        monkeypatch.setattr(annual_templates,'select_for_profile',selected);monkeypatch.setattr(annual_templates,'select_frozen',selected)
    if entry=='standalone':
        doc=document(PAGES,{})
        material_writing.ensure_document_resolution(doc,profile);result=profile.writing_scope
    else:result=asyncio.run(material_writing.load('test','a'*64,profile,PAGES,frozen_bindings=[] if entry=='frozen' else None))
    assert result['snapshot']['available'] and result['status']=='conflict'
    assert selection[0].report_year.source=='cover_title'
    assert before['report_year']==profile.to_dict()['report_year']


@pytest.mark.parametrize('filename,pages',[('示例2023年度决算.pdf',PAGES),('示例2024年度决算.pdf',['年度决算','正文']),('示例2024预算.pdf',PAGES)])
def test_r1_filename_alone_or_opposite_does_not_select(monkeypatch,filename,pages):
    monkeypatch.setenv('GBC_TEMPLATE_SCOPE','true');monkeypatch.setenv('GBC_TEMPLATE_EXPORT','test-only');monkeypatch.delenv('DATABASE_URL',raising=False)
    def forbidden(*args):raise AssertionError('没有原件同向依据，不得选模板')
    monkeypatch.setattr(annual_templates,'offline_for_profile',forbidden)
    profile=resolve_document_profile(filename=filename,page_texts=pages)
    value=asyncio.run(material_writing.load('test','a'*64,profile,pages))
    assert not value['snapshot']['available'] and not value['template_applicability']['verified']


def test_r2_report_comparison_and_name_slots_preserve_citation_amount_and_code():
    text='2024年度依据《2024年办法》，比2023年增加2024.00万元，编码2024，上海市示例局说明'
    name='上海市示例局';start=text.index('2023');org=text.index(name)
    anchor={'text':text,'annual_slot':True,'organization_slot':name,'slots':[
        {'kind':'report_year','start':0,'end':4}, {'kind':'comparison_year','start':start,'end':start+4},
        {'kind':'organization_name','start':org,'end':org+len(name)}]}
    assert instantiate_anchor(anchor,2024,2025,'上海市新局')=='2025年度依据《2024年办法》，比2024年增加2024.00万元，编码2024，上海市新局说明'
    changed=copy.deepcopy(anchor);citation=text.index('2024',4);changed['slots']=[{'kind':'report_year','start':citation,'end':citation+4}]
    with pytest.raises(ValueError,match='固定文献'):validated_slots(changed,2024)
    for start in (text.index('2024.00'),text.index('2024，')):
        changed['slots']=[{'kind':'report_year','start':start,'end':start+4}]
        with pytest.raises(ValueError,match='金额和科目'):validated_slots(changed,2024)


def test_r2_runtime_fixed_citation_heading_and_legacy_flag():
    anchor={'kind':'normative_section','scope':'unit','text':'2024年度依据《2024年办法》执行情况说明','basis':'test-only','annual_slot':True,
        'slots':[{'kind':'report_year','start':0,'end':4}]}
    doc=document(['2025年度决算','2025年度依据《2024年办法》执行情况说明'],fixture_resolution([anchor]))
    assert additional_anchor_issues(CMM008_TemplateResidue(),doc,'normative_section')==([],[])
    anchor.pop('slots')
    issues,pending=additional_anchor_issues(CMM008_TemplateResidue(),doc,'normative_section')
    assert not issues and any('具体位置' in p for p in pending)


def test_r3_orphan_ocr_cannot_determine_writing_scope():
    artifact=ParseArtifact(source_file_hash='a'*64,page_count=2,text_blocks=[
        ParsedTextBlock('orphan-cover',1,'2024年度部门决算',confidence=.99,extraction_method='ocr'),
        ParsedTextBlock('orphan-heading',2,'2024年度部门决算表',confidence=.99,extraction_method='ocr'),
        ParsedTextBlock('orphan-note',2,'本表反映部门本年度收入。',confidence=.99,extraction_method='ocr')])
    original=artifact.to_dict();pages,spans=material_writing.qualified_identity_view(['',''],artifact)
    result=resolve_material_scope(evidence=material_writing.collect(pages,source_spans=spans),template_qualified=True)
    assert result.status=='insufficient' and result.value is None and pages==['','']
    assert len([s for s in spans if s.get('qualified') is False])==3
    assert not any(s['source_method']=='ocr' for s in spans) and artifact.to_dict()==original


@pytest.mark.parametrize('tamper',['none','page','original','model','request','prediction','geometry','score_nan','score_inf','score_bool','score_low','score_overflow'])
def test_r3_binding_not_score_controls_eligibility(tamper):
    artifact=bound_identity_artifact([['2024年度部门决算'],['2024年度部门决算表','本表反映部门本年度收入。']])
    assert all(bound_ocr_block(b,artifact) for b in artifact.text_blocks)
    if tamper=='page':
        for b in artifact.text_blocks:b.page_number=3-b.page_number
    elif tamper=='original':artifact.source_file_hash='f'*64
    elif tamper=='model':artifact.ocr_model_fingerprint='f'*64
    elif tamper.startswith('score_'):
        for b in artifact.text_blocks:b.confidence={'score_nan':float('nan'),'score_inf':float('inf'),'score_bool':True,'score_low':.97,'score_overflow':1.01}[tamper]
    else:
        for t in artifact.coordinate_transforms.values():
            if tamper=='request':t['page_request']['input_sha256']='f'*64
            elif tamper=='prediction':t['ocr_response']['raw_prediction_sha256']='f'*64
            elif tamper=='geometry':t['pixel_to_pdf_matrix'][4]=25
    pages,spans=material_writing.qualified_identity_view(['',''],artifact)
    result=resolve_material_scope(evidence=material_writing.collect(pages,source_spans=spans),template_qualified=True)
    assert result.status==('confirmed' if tamper=='none' else 'insufficient')
    assert len([s for s in spans if s['source_method']=='ocr'])==(3 if tamper=='none' else 0)


@pytest.mark.parametrize('kind,text,other',[('normative_section','本报告尚未提供新增政策执行情况说明。',False),('normative_table','说明：测试新增披露表本年未编制。',True)])
def test_r4_narrative_mentions_never_complete_structure(kind,text,other):
    title='新增政策执行情况说明' if kind=='normative_section' else '测试新增披露表'
    doc=document(['2025年度决算',text],fixture_resolution([{'kind':kind,'scope':'all','text':title,'basis':'test-only'}]))
    if other:doc.page_tables=[[],[[['其他项目','金额'],['其他事项','1.00']]]]
    issues,pending=additional_anchor_issues(CMM008_TemplateResidue(),doc,kind)
    assert issues or pending


def table_document():
    anchor={'kind':'normative_table','scope':'all','text':'测试新增披露表','basis':'test-only'}
    doc=document(['2025年度决算','测试新增披露表'],fixture_resolution([anchor]))
    table=ParsedTable('TEST','测试新增披露表',2,0,(20,50,400,200),1,1,'native_text',None,[['科目','金额'],['测试','1']],[],fragment_uid='source-fragment')
    doc.parse_artifact=ParseArtifact(source_file_hash='a'*64,page_count=2,table_fragments=[table],logical_tables=[
        LogicalTableInstance('source-instance','TEST','测试新增披露表',['source-fragment'],2,2)])
    return doc


@pytest.mark.parametrize('change',['none','other-table','wrong-page','no-instance','unbound-ocr'])
def test_r4_title_requires_corresponding_source_table(change):
    doc=table_document();table=doc.parse_artifact.table_fragments[0]
    if change=='other-table':table.title='另一个表'
    elif change=='wrong-page':table.page_number=1
    elif change=='no-instance':doc.parse_artifact.logical_tables=[]
    elif change=='unbound-ocr':table.extraction_method='ocr'
    issues,pending=additional_anchor_issues(CMM008_TemplateResidue(),doc,'normative_table')
    assert not issues
    assert bool(pending)==(change!='none')


@pytest.mark.parametrize('title',['三、新增政策执行情况说明','新增政策执行\n情况说明'])
def test_r4_real_section_heading_or_wrapping_is_accepted(title):
    anchor={'kind':'normative_section','scope':'all','text':'新增政策执行情况说明','basis':'test-only'}
    doc=document(['2025年度决算',title],fixture_resolution([anchor]))
    assert additional_anchor_issues(CMM008_TemplateResidue(),doc,'normative_section')==([],[])


@pytest.mark.parametrize('prefix',['本报告尚未提供','正文详见','引用：'])
def test_r4_wrapped_reference_is_not_a_heading(prefix):
    anchor={'kind':'normative_section','scope':'all','text':'新增政策执行情况说明','basis':'test-only'}
    doc=document(['2025年度决算',prefix+'\n新增政策执行情况说明'],fixture_resolution([anchor]))
    issues,pending=additional_anchor_issues(CMM008_TemplateResidue(),doc,'normative_section')
    assert issues or pending


def test_r2_semantic_reuse_preserves_fixed_reference_year():
    def config(year,citation):
        return {'anchors':[{'kind':'normative_section','scope':'all','basis':'test-only','source_report_year':year,
            'text':f'{year}年度依据《{citation}年办法》执行情况说明','slots':[{'kind':'report_year','start':0,'end':4}]}]}
    assert annual_templates.semantic_config(config(2024,2024))==annual_templates.semantic_config(config(2025,2024))
    assert annual_templates.semantic_config(config(2024,2024))!=annual_templates.semantic_config(config(2025,2025))


@pytest.mark.asyncio
async def test_r2_review_records_original_slot_positions(monkeypatch,tmp_path):
    from uuid import uuid4
    from fastapi import HTTPException
    unit,dept=str(uuid4()),str(uuid4());raw='原件说明\n2024年度依据《2024年办法》执行情况说明'
    monkeypatch.setenv('GBC_TEMPLATE_STORAGE',str(tmp_path))
    monkeypatch.setattr(annual_templates,'text',AsyncMock(return_value=raw))
    monkeypatch.setattr(annual_templates,'source',AsyncMock(return_value=({'source_report_year':2024},{},tmp_path)))
    content={'unit_source_id':unit,'department_source_id':dept}
    anchor={'kind':'normative_section','scope':'all','source_id':unit,'text':raw.split('\n')[1],'basis':'test-only visual review',
        'slots':[{'kind':'report_year','start':0,'end':4}]}
    checked=await annual_templates.reviewed_anchors(None,content,{'anchors':[anchor]})
    assert checked[0]['slots'][0]['source_quote']=='2024'
    assert checked[0]['slots'][0]['source_start']==raw.index('2024')
    anchor['slots']=[{'kind':'report_year','start':anchor['text'].index('2024',4),'end':anchor['text'].index('2024',4)+4}]
    with pytest.raises(HTTPException) as ex:await annual_templates.reviewed_anchors(None,content,{'anchors':[anchor]})
    assert ex.value.status_code==422
