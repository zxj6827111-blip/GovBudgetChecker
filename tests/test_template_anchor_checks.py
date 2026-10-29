import pytest
from fastapi import HTTPException
from src.services.annual_templates import reviewed_slot_changes,validate_compatibility,ALGORITHM
from src.services.template_anchor_checks import additional_anchor_issues
from src.engine.checklist_text_rules import CMM008_TemplateResidue
from src.engine.checklist_identity_rules import CMM027_TemplatePositionScope
from src.engine.rule_outcome import RuleDeferred
from tests.test_material_writing_rules import document


def fixture_resolution(anchors=(),prompts=(),status='confirmed',value='unit'):
    return {'status':status,'value':value,'snapshot':{'available':True,'source_report_year':2024,'target_year':2025,'package_id':'test-only',
        'capability_scope':{'structure':True,'residue':True},'content':{'config':{'anchors':list(anchors),'prompts':list(prompts)}}}}


def test_additional_requirement_keeps_scope_and_source_basis():
    anchor={'kind':'normative_section','scope':'unit','text':'2024年度测试必备说明','annual_slot':True,'slots':[{'kind':'report_year','start':0,'end':4}],'basis':'test-only authority','source_id':'test-only'}
    doc=document(['2025年度决算','第二部分决算表'],fixture_resolution([anchor]))
    issues,pending=additional_anchor_issues(CMM008_TemplateResidue(),doc,'normative_section')
    assert not pending and len(issues)==1 and '2025年度' in issues[0].message
    doc.document_profile.writing_scope=fixture_resolution([anchor],status='insufficient',value=None)
    issues,pending=additional_anchor_issues(CMM008_TemplateResidue(),doc,'normative_section')
    assert not issues and pending


@pytest.mark.parametrize('kind,rule',[('normative_section','CMM023_SectionTemplate'),('normative_table','CMM021_TableTitleNormative')])
def test_pending_anchor_requirement_is_an_incomplete_receipt_not_an_execution_error(kind,rule):
    # 写法依据不足时附加要求待核，规则须诚实记为未完成，而不是因缺少导入变成执行错误。
    from src.engine import checklist_content_rules
    anchor={'kind':kind,'scope':'unit','text':'测试附加要求','basis':'test-only authority','source_id':'test-only'}
    doc=document(['2025年度决算','第二部分决算表'],fixture_resolution([anchor],status='insufficient',value=None))
    with pytest.raises(RuleDeferred) as caught:getattr(checklist_content_rules,rule)().apply(doc)
    assert '适用条件未核实' in caught.value.detail


def test_optional_content_never_becomes_missing_requirement():
    anchor={'kind':'optional','scope':'all','text':'测试可选采购段落','basis':'test-only optional'}
    doc=document(['2024年度决算','正文'],fixture_resolution([anchor]))
    assert additional_anchor_issues(CMM008_TemplateResidue(),doc,'normative_section')==([],[])


def test_table_name_reference_without_table_is_not_passed():
    anchor={'kind':'normative_table','scope':'all','text':'测试附加表','basis':'test-only standard'}
    doc=document(['2024年度决算','详见测试附加表'],fixture_resolution([anchor]))
    issues,pending=additional_anchor_issues(CMM008_TemplateResidue(),doc,'normative_table')
    assert issues or pending


def test_full_prompt_and_normal_empty_statement_are_distinguished():
    prompt='三公经费说明中列明公务接待批次、人次'
    result=fixture_resolution(prompts=[prompt])
    bad=document(['2024年度决算',prompt+'。本年度无公务接待。'],result)
    assert any('已审核模板' in i.message for i in CMM008_TemplateResidue().apply(bad))
    for text in ['本单位本年度无公务接待支出。','引用：“'+prompt+'”属于编报提示语。']:
        clean=document(['2024年度决算',text],result)
        assert not any('已审核模板' in i.message for i in CMM008_TemplateResidue().apply(clean))


def test_conditional_table_prompt_is_reported_once_and_not_as_missing_statement():
    from src.engine.native_disclosure_semantics import check_disclosure_semantics
    from src.engine.rules_v33 import R33234_NarrativePercentConsistency
    from src.engine.checklist_final_rules import CMM026_EmptyNoteOnDataTable
    prompt='无政府性基金预算财政拨款收入支出的，仍保留该表格式，并在本表下方作下述说明：'
    pages=['2024年度决算','八、政府性基金预算财政拨款收入支出决算表\n'+prompt+'本单位本年度没有政府性基金预算财政拨款收入和支出，故本表无数据。']
    doc=document(pages,fixture_resolution(prompts=[prompt]))
    assert not any('已审核模板' in i.message for i in CMM008_TemplateResidue().apply(doc))
    native,_=check_disclosure_semantics(R33234_NarrativePercentConsistency(),doc)
    assert len([i for i in native if '仍保留该表格式' in i.message])==1
    # 提示语之后的事实说明仍是正常空表说明；提示语本身不构成“有数据表保留空表说明”。
    only_prompt=document(['2024年度决算','八、政府性基金预算财政拨款收入支出决算表\n'+prompt],fixture_resolution(prompts=[prompt]))
    assert not CMM026_EmptyNoteOnDataTable().apply(only_prompt)
    budget=document(pages,{**fixture_resolution(prompts=[prompt]),'domain':'budget'})
    budget.report_kind='budget'
    assert any('已审核模板' in i.message for i in CMM008_TemplateResidue().apply(budget))


def test_corrupted_template_receipt_cannot_be_not_applicable():
    doc=document(['2024年度决算','正文'],{'snapshot':{'available':False,'reason':'发布包校验失败'}})
    with pytest.raises(RuleDeferred):CMM027_TemplatePositionScope().apply(doc)


def slot_content(old,new,category):
    return {'source_report_year':2025,'previous_source_report_year':2024,
        'review_record':{'slot_reviews':{'unit':[category]}},
        'changes':{'unit':[{'old':[{'text':old}],'new':[{'text':new}]}]}}


def test_only_reviewed_slots_can_reuse_package():
    assert reviewed_slot_changes(slot_content('2024年度决算表','2025年度决算表','report_year'))
    assert reviewed_slot_changes(slot_content('上海市XX局2024年度决算表','上海市测试局2024年度决算表','organization_name'))
    assert not reviewed_slot_changes(slot_content('单位：万元','单位：元','report_year'))
    assert not reviewed_slot_changes(slot_content('单位：万元','单位：元','organization_name'))
    assert not reviewed_slot_changes(slot_content('2024年度决算表','2025年度部门决算表','report_year'))
    assert not reviewed_slot_changes(slot_content('2024年度决算表','2025年度决算表','pending'))


def test_compatibility_denominator_and_hash_are_required():
    package={'content_sha256':'hash','source_report_year':2024}
    proof={'report_reference':'test-only','independent_labels':True,'boundary_cases_passed':True,
        'independent_sample_count':99,'automatic_verified_count':40,'automatic_errors':0,
        'algorithm':ALGORITHM,'package_hash':'hash','source_report_year':2024,'target_year':2025,
        'capability_scope':{'writing_auto':True}}
    def check(value):validate_compatibility(value,package,target_year=2025,capabilities={'writing_auto':True})
    with pytest.raises(HTTPException):check(proof)
    check({**proof,'independent_sample_count':100})
    check({**proof,'independent_sample_count':12,'automatic_verified_count':4,'corpus_size':12,'all_corpus_verified':True})
    with pytest.raises(HTTPException):check({**proof,'independent_sample_count':100,'automatic_errors':1})
    with pytest.raises(HTTPException):check({**proof,'independent_sample_count':100,'package_hash':'wrong'})


def test_geography_and_overlay_keep_municipal_norms_without_moving_facts():
    from src.services.annual_templates import geography,compose_snapshots,sha
    from src.schemas.document_profile import DocumentProfile,ProfileField
    p=DocumentProfile(jurisdiction=ProfileField(value='上海市普陀区'))
    keys=[{'region':'上海市','overlay':'','capability_scope':{'structure':True}},
        {'region':'上海市','overlay':'普陀区','capability_scope':{'writing_auto':True}}]
    assert geography(keys,p,['上海市普陀区测试局2024年度决算'])[:2]==('上海市','普陀区')
    base={'available':True,'package_id':'base','content_sha256':'basehash','revision':1,'content':{'config':{'anchors':[{'text':'市级必备规范'}],'prompts':['市级提示语']}},'capability_scope':{'structure':True}}
    overlay={'available':True,'package_id':'overlay','content':{'config':{'anchors':[{'text':'区级附加规范'}],'prompts':[]}},'capability_scope':{'writing_auto':True}}
    merged=compose_snapshots(base,overlay)
    assert len(merged['effective_config']['anchors'])==2 and merged['capability_scope']['structure']
    assert merged['effective_config_sha256']==sha(merged['effective_config'])
    assert not overlay['capability_scope'].get('structure') and 'effective_config' not in overlay


def test_d2_near_prefix_does_not_expand_low_scope():
    from src.engine.checklist_identity_rules import CMM018_ScopeTerminology
    from tests.test_material_writing_rules import resolution
    from src.services.template_scope import HumanDecision
    pages=['2024年度决算','十、预算绩效管理情况\n本部门建立了如下预算绩效管理制度化方案。']
    issues=CMM018_ScopeTerminology().apply(document(pages,resolution(pages,HumanDecision('unit','test-only',True))))
    assert issues and all(i.severity!='low' for i in issues)


def test_year_slot_does_not_rewrite_amounts_codes_or_fixed_citations():
    assert reviewed_slot_changes(slot_content('2024年收入，比2023年增加','2025年收入，比2024年增加','report_and_comparison_year'))
    assert reviewed_slot_changes(slot_content('比2023年增加','比2024年增加','comparison_year'))
    assert not reviewed_slot_changes(slot_content('预算2024.00万元','预算2025.00万元','report_year'))
    assert not reviewed_slot_changes(slot_content('依据《2024年办法》','依据《2025年办法》','report_year'))
    assert not reviewed_slot_changes(slot_content('科目编码2024','科目编码2025','report_year'))


def test_compound_annual_and_name_slots_still_reject_amount_changes():
    old='上海市XX局2024年收入，比2023年增加10.00万元'
    assert reviewed_slot_changes(slot_content(old,'上海市测试局2025年收入，比2024年增加10.00万元','annual_and_name'))
    assert not reviewed_slot_changes(slot_content(old,'上海市测试局2025年收入，比2024年增加11.00万元','annual_and_name'))
