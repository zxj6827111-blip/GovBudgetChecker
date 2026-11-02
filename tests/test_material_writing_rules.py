from dataclasses import asdict
import pytest
from src.services.material_writing import collect
from src.services.template_scope import HumanDecision,resolve_material_scope
from src.schemas.document_profile import DocumentProfile,ProfileField
from src.engine.rules_v33 import Document
from src.engine.checklist_identity_rules import CMM018_ScopeTerminology,CMM019_CoverElements,CMM027_TemplatePositionScope
from src.engine.rule_outcome import RuleDeferred
from src.engine.rule_registry import rule_catalog


def document(pages,resolution):
    return Document('filename-department-does-not-decide.pdf',len(pages),100,pages,[[] for _ in pages],[None]*len(pages),[[]]*len(pages),report_kind='final',
        document_profile=DocumentProfile(report_kind=ProfileField(value='final'),writing_scope=resolution,jurisdiction=ProfileField(value='上海市普陀区')))


def resolution(pages,human=None):
    result=asdict(resolve_material_scope(evidence=collect(pages),template_qualified=True,human=human))
    result['snapshot']={'available':True,'package_id':'test-package','revision':1,'capability_scope':{'position_checks':True},
        'content':{'config':{'d2':'low','native':{'sentence':'本部门建立了如下预算绩效管理制度'}}}}
    return result


def test_position_conflict_then_human_confirmation_groups_notes():
    pages=['2024年度决算','2024年度决算表\n本表反映部门本年度收入。','2024年度决算情况说明\n本表反映部门本年度支出。']
    assert resolution(pages)['status']=='conflict'
    value=resolution(pages,HumanDecision('unit','test-only',True));doc=document(pages,value)
    issues=CMM027_TemplatePositionScope().apply(doc)
    assert len(issues)==1 and issues[0].location['occurrences']==2
    assert len(issues[0].location['locations'])==2


def test_unknown_keeps_independent_findings_and_cover_issues():
    pages=['2023年度决算','2024年度决算表\n市级预算主管部门','2024年度决算情况说明']
    doc=document(pages,resolution(pages,HumanDecision('unknown','test-only')))
    with pytest.raises(RuleDeferred) as caught:CMM018_ScopeTerminology().apply(doc)
    assert caught.value.partial_issues
    with pytest.raises(RuleDeferred) as caught:CMM019_CoverElements().apply(doc)
    assert any('年度' in i.message for i in caught.value.partial_issues)


def test_d2_low_does_not_exempt_other_department_terms():
    pages=['2024年度决算','十、预算绩效管理情况\n本部门建立了如下预算绩效管理制度：测试制度。\n十一、其他重要事项\n本部门面向中小企业预留资金。']
    doc=document(pages,resolution(pages,HumanDecision('unit','test-only',True)))
    issues=CMM018_ScopeTerminology().apply(doc)
    assert any(i.severity=='low' for i in issues)
    assert any(i.severity=='warn' and '本部门' in i.message for i in issues)


def test_registry_and_obligation_are_connected():
    entry=next(r for r in rule_catalog()['rules'] if r['rule_id']=='CMM-027')
    assert 'OBL-STRUCT-TERMINOLOGY' in entry['obligation_ids']


def test_organization_prefixed_headings_are_not_lost():
    pages=['上海市测试局2024年度决算','第二部分 上海市测试局2024 年度决算表\n本表反映部门本年度收入。','第三部分 上海市测试局2024 年度决算情况说明']
    assert resolution(pages)['status']=='conflict'
    assert {e.kind for e in collect(pages)}=={'heading','table_note'}


def test_qualified_ocr_view_is_separate_deduplicated_and_shared():
    from types import SimpleNamespace
    from src.schemas.parse_artifact import ParsedTextBlock
    from src.services.material_writing import qualified_identity_view
    from tests.annual_template_p1_helpers import bound_identity_artifact
    artifact=bound_identity_artifact([['2024年度部门决算'],['2024年度决算表','本表反映单位本年度收入。']])
    artifact.text_blocks.append(ParsedTextBlock('repeat',2,'2024年度决算表',confidence=.99,extraction_method='ocr'))
    raw=['',''];pages,spans=qualified_identity_view(raw,artifact)
    assert raw==['',''] and pages[1].count('2024年度决算表')==1
    evidence=collect(pages,source_spans=spans)
    value=asdict(resolve_material_scope(evidence=evidence,template_qualified=True))
    assert value['status']=='conflict'
    assert all(e.source_blocks[0]['source_method']=='ocr' for e in evidence)
    doc=document(raw,resolution(pages,HumanDecision('unit','test-only',True)))
    doc.writing_page_texts=pages;doc.writing_source_spans=spans
    with pytest.raises(RuleDeferred) as ex:CMM019_CoverElements().apply(doc)
    assert any('误写' in i.message for i in ex.value.partial_issues)
    assert doc.page_texts==raw


def test_quoted_declarations_and_narrative_note_references_are_not_strong_signals():
    pages=['2024年度决算','第二部分2024年度决算表\n引用：“本报告为部门决算”。\n请参考本表反映部门本年度收入。\n说明：本表反映单位本年度收入。']
    proof=collect(pages)
    assert all(e.scope=='unit' for e in proof)
    assert {e.kind for e in proof}=={'heading','table_note'}


# ---------------------------------------------------------------------------
# 阶段 0：所选层级作为检查基准 + “写法全面相反”待复核分支
# ---------------------------------------------------------------------------

ARCHIVE_BASIS={'organization_id':'org-test','organization_name':'上海市测试中心',
               'level':'unit','source':'upload_selection'}


def native_spans(pages):
    return [{'page':p,'start':0,'end':len(t),'source_method':'native_text'} for p,t in enumerate(pages,1)]


def archive_resolution(pages,level='unit',cover=None,org=None):
    result=asdict(resolve_material_scope(evidence=collect(pages,source_spans=native_spans(pages)),
                                         template_qualified=False,selected_level=level,
                                         cover_name=cover,selected_org_name=org))
    result['snapshot']={'available':False,'reason':'模板能力关闭'}
    result['archive_basis']=dict(ARCHIVE_BASIS, level=level,
                                 organization_name=org or ARCHIVE_BASIS['organization_name'])
    result['domain']='final'
    return result


def archive_document(pages,level='unit'):
    doc=document(pages,archive_resolution(pages,level))
    doc.writing_page_texts=list(pages)
    doc.writing_source_spans=native_spans(pages)
    return doc


# 全面相反：正文标题与表注全部是部门写法，所选层级是单位。
CONTRAST_PAGES=['2024年度单位决算',
                '上海市测试中心2024 年度部门决算表\n本表反映部门本年度收入。',
                '上海市测试中心2024 年度部门决算情况说明\n本部门经费情况见相关说明。\n使用市级财政拨款的情况已另行说明。']


def test_cmm019_comprehensive_contrast_emits_single_manual_review_without_genre_warns():
    doc=archive_document(CONTRAST_PAGES[:3])
    issues=CMM019_CoverElements().apply(doc)
    reviews=[i for i in issues if i.severity=='manual_review']
    warns=[i for i in issues if i.severity=='warn' and ('误写' in i.message or '漏写' in i.message)]
    assert len(reviews)==1 and '相反' in reviews[0].message and '请核对归档' in reviews[0].message
    assert not warns, '全面相反时文种子项不得再出 warn'
    assert reviews[0].location['writing_contrast']['comprehensive_contrast'] is True


def test_cmm019_other_subitems_still_run_under_contrast():
    # 名称子项照常执行：封面缺单位全称仍出 warn；年度子项正常核对。
    doc=archive_document(['2023年度单位决算',
                          '上海市测试中心2024 年度部门决算表\n本表反映部门本年度收入。',
                          '上海市测试中心2024 年度部门决算情况说明\n上海市测试中心经费情况见相关说明。'])
    issues=CMM019_CoverElements().apply(doc)
    assert any(i.severity=='manual_review' and '相反' in i.message for i in issues)
    assert any(i.severity=='warn' and '全称' in i.message for i in issues)


def test_cmm019_deferred_partial_issues_carry_no_genre_warn():
    from src.engine.rule_outcome import RuleDeferred
    # 缺少正文独立名称依据 → RuleDeferred；partial_issues 只含待复核，不含文种 warn。
    doc=archive_document(['2024年度单位决算',
                          '2024 年度部门决算表\n本表反映部门本年度收入。',
                          '2024 年度部门决算情况说明'])
    with pytest.raises(RuleDeferred) as caught:
        CMM019_CoverElements().apply(doc)
    assert caught.value.partial_issues
    assert all(i.severity!='warn' for i in caught.value.partial_issues)
    assert any(i.severity=='manual_review' and '相反' in i.message for i in caught.value.partial_issues)


def test_cmm018_merges_scope_hits_into_single_manual_review():
    doc=archive_document(CONTRAST_PAGES)
    issues=CMM018_ScopeTerminology().apply(doc)
    merged=[i for i in issues if i.severity=='manual_review']
    warns=[i for i in issues if i.severity=='warn' and '主体口径混用' in i.message]
    assert len(merged)==1 and '相反' in merged[0].message
    assert not warns, '全面相反时主体用语不得再逐条 warn'
    # 市级/区级用语子项照常执行。
    assert any(i.severity=='warn' and '市级' in i.message for i in issues)
    assert merged[0].location['writing_contrast']['opposite_pages']==[2,3]


def test_cmm018_keeps_warns_when_contrast_is_not_comprehensive():
    # 混写（正文出现所选一侧证据）→ 不算全面相反，照常按所选层级检查。
    pages=list(CONTRAST_PAGES)+['2024 年度单位决算表']
    doc=archive_document(pages)
    issues=CMM018_ScopeTerminology().apply(doc)
    assert any(i.severity=='warn' and '主体口径混用' in i.message for i in issues)
    assert not any(i.severity=='manual_review' for i in issues)


def test_single_space_toc_entry_no_longer_blocks_comprehensive_contrast():
    # 审核修正（P2）：目录续页条目（单空格+页码）不得当作所选一侧正文证据，
    # 否则本应转 manual_review 的 CMM-018 会继续逐条 warn。
    pages=['2024年度单位决算',
           '第二部分 测试中心2024 年度单位决算表 3',
           '2024 年度部门决算表\n本表反映部门本年度收入。',
           '2024 年度部门决算情况说明\n本部门经费情况见相关说明。']
    doc=archive_document(pages)
    issues=CMM018_ScopeTerminology().apply(doc)
    assert any(i.severity=='manual_review' and '相反' in i.message for i in issues)
    assert not any(i.severity=='warn' and '主体口径混用' in i.message for i in issues)


def test_wrapped_note_with_footer_number_keeps_manual_review():
    # 审核第三轮（P2）复现场景：无句号表注 + 下一行独立页码。
    # 修复前：表注被当目录条目排除 → 全面相反不成立 → CMM-018/019 退回 warn。
    from src.engine.rule_outcome import RuleDeferred
    pages=['2024年度单位决算',
           '2024 年度部门决算表\n本表反映部门本年度收入情况\n12\n',
           '2024 年度部门决算情况说明\n本部门经费情况见相关说明。']
    doc=archive_document(pages)
    issues=CMM018_ScopeTerminology().apply(doc)
    assert any(i.severity=='manual_review' and '相反' in i.message for i in issues)
    assert not any(i.severity=='warn' and '主体口径混用' in i.message for i in issues)
    # CMM-019：缺正文独立名称依据 → RuleDeferred，partial_issues 仍带待复核、无文种 warn。
    with pytest.raises(RuleDeferred) as caught:
        CMM019_CoverElements().apply(doc)
    assert any(i.severity=='manual_review' and '相反' in i.message for i in caught.value.partial_issues)
    assert all(i.severity!='warn' for i in caught.value.partial_issues)


@pytest.mark.parametrize('toc', ['2024年度单位决算情况说明 12',
                                 '2024年度单位决算情况\n说明 12'],
                         ids=['single_line', 'wrapped_title'])
def test_toc_title_wrapping_does_not_change_archive_review(toc):
    # 审核第四轮（P2，对齐审查方边界用例）：同一目录条目折行与否，
    # CMM-018/019 都必须是待复核（全面相反成立），不得退回逐条 warn。
    from src.engine.rule_outcome import RuleOutcomeSignal
    pages=['上海市测试中心2024年度部门决算', toc,
           '上海市测试中心2024年度部门决算表\n本表反映部门本年度收入。',
           '上海市测试中心2024年度部门决算情况说明\n本部门经费情况见相关说明。']
    doc=archive_document(pages)
    for rule in (CMM018_ScopeTerminology(), CMM019_CoverElements()):
        try:
            issues=rule.apply(doc)
        except RuleOutcomeSignal as signal:
            issues=signal.partial_issues
        assert any(i.severity=='manual_review' for i in issues), (rule.code, toc)
        assert all(i.severity!='warn' for i in issues), (rule.code, toc)


def test_human_decision_keeps_existing_warn_behavior():
    # 有人工写法决定时以人工为准：不做全面相反合并，行为与现状一致。
    pages=CONTRAST_PAGES
    spans=native_spans(pages)
    result=asdict(resolve_material_scope(evidence=collect(pages,source_spans=spans),
                                         template_qualified=False,human=HumanDecision('unit','t-only',True)))
    result['snapshot']={'available':False,'reason':'模板能力关闭'}
    doc=document(pages,result)
    issues=CMM018_ScopeTerminology().apply(doc)
    assert any(i.severity=='warn' and '主体口径混用' in i.message for i in issues)
    assert not any(i.severity=='manual_review' for i in issues)


def test_v33_236_unaffected_by_archive_resolution():
    from src.engine.rules_v33 import R33236_DocumentScopeTerminology
    pages=['2024年度单位决算','2024 年度部门决算表','2024 年度部门决算情况说明\n本年部门决算安排增加。']
    rule=R33236_DocumentScopeTerminology()
    plain=document(pages,{'status':'insufficient','value':None,'source':'unresolved'})
    with_archive=archive_document(pages)
    issues_plain=rule.apply(plain)
    issues_archive=rule.apply(with_archive)
    assert [i.message for i in issues_plain]==[i.message for i in issues_archive]
    assert issues_archive, 'V33-236 自身结论应保持不变'


def test_load_records_archive_basis_and_confirms_selected_level():
    # 无库环境：所选层级经 load() 冻结为 source=archive 的检查基准并写入依据。
    import asyncio
    from src.services.material_writing import load
    from src.schemas.document_profile import DocumentProfile,ProfileField
    pages=['2024年度单位决算','2024 年度部门决算表\n本表反映部门本年度收入。']
    profile=DocumentProfile(report_kind=ProfileField(value='final'))
    result=asyncio.run(load('job-x','hash-x',profile,pages,'unit',archive_basis=dict(ARCHIVE_BASIS)))
    assert result['status']=='confirmed' and result['source']=='archive'
    assert result['value']=='unit' and result['cover_independent_scope']=='unit'
    assert result['archive_basis']==ARCHIVE_BASIS
    assert result['reason']=='upload_selection_level'
    # 层级为 city（或查不到）时不使用所选层级：无正文明示即回到依据不足。
    city_basis=dict(ARCHIVE_BASIS,level='city')
    neutral=['2024年度材料封面','2024 年度部门决算表\n本表反映部门本年度收入。']
    result_city=asyncio.run(load('job-x','hash-x',profile,neutral,'city',archive_basis=city_basis))
    assert result_city['status']=='insufficient' and result_city['source']=='unresolved'
    assert result_city['archive_basis']==city_basis
    # 原路径不受影响：封面声明仍按 declaration 裁决。
    result_decl=asyncio.run(load('job-x','hash-x',profile,pages,'city',archive_basis=city_basis))
    assert result_decl['source']=='declaration' and result_decl['value']=='unit'


def test_load_rejects_selected_level_on_cover_subject_mismatch():
    # 封面主体是所选组织名加后缀（下属单位）：不用所选层级，记录 archive_subject_mismatch。
    import asyncio
    from src.services.material_writing import load
    from src.schemas.document_profile import DocumentProfile,ProfileField
    profile=DocumentProfile(report_kind=ProfileField(value='final'))
    pages=['上海市测试中心执法总队2024 年度决算',
           '2024 年度部门决算表\n本表反映部门本年度收入。',
           '2024 年度部门决算情况说明']
    dept_basis=dict(ARCHIVE_BASIS,level='department')
    mismatch=asyncio.run(load('job-x','hash-x',profile,pages,'department',
                              archive_basis=dept_basis,cover_name='上海市测试中心执法总队'))
    assert mismatch['status']=='insufficient' and mismatch['source']=='unresolved'
    assert mismatch['archive_subject_mismatch'] is True
    assert mismatch['cover_independent_scope'] is None
    # 封面主体一致（含允许差异）时照常使用所选层级。
    matched=asyncio.run(load('job-x','hash-x',profile,pages,'department',
                             archive_basis=dept_basis,cover_name='附件1上海市测试中心'))
    assert matched['source']=='archive' and matched['value']=='department'
    assert matched['archive_subject_mismatch'] is False


def test_rules_report_subject_mismatch_in_deferred_reason():
    from src.engine.rule_outcome import RuleDeferred
    # 封面主体不一致 → 写法基准回到未完成；CMM-018/019 的未完成原因附核对归档提示。
    pages=['上海市测试中心执法总队2024 年度决算',
           '2024 年度部门决算表\n本表反映部门本年度收入。',
           '2024 年度部门决算情况说明']
    res=archive_resolution(pages,level='department',cover='上海市测试中心执法总队',
                           org='上海市测试中心')
    assert res['archive_subject_mismatch'] is True and res['status']=='insufficient'
    doc=document(pages,res)
    with pytest.raises(RuleDeferred) as caught:
        CMM019_CoverElements().apply(doc)
    assert '封面主体与所选组织不一致' in caught.value.detail
    with pytest.raises(RuleDeferred) as caught:
        CMM018_ScopeTerminology().apply(doc)
    assert '封面主体与所选组织不一致' in caught.value.detail


def test_body_year_counts_headings_split_across_lines():
    """PDF 标题常在年份和“年度决算表”之间换行；跨行标题必须计入正文锚点。"""
    from types import SimpleNamespace
    from src.engine.checklist_identity_rules import body_year
    pages = [
        "某单位\n2025 年度决算",
        "第二部分 某单位 2025\n年度决算表",
        "第三部分 某单位 2025\n年度决算情况说明",
    ]
    assert body_year(SimpleNamespace(page_texts=pages)) == 2025


def test_body_year_still_needs_two_anchors_and_rejects_typo_year():
    from types import SimpleNamespace
    from src.engine.checklist_identity_rules import body_year
    assert body_year(SimpleNamespace(page_texts=["封面", "2025年度决算表"])) is None
    assert body_year(SimpleNamespace(page_texts=["封面", "22025年度决算表", "2025年度决算表"])) is None
