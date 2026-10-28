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
    blocks=[ParsedTextBlock('cover',1,'2024年度部门决算',confidence=.99,extraction_method='ocr'),
        ParsedTextBlock('h',2,'2024年度决算表',confidence=.99,extraction_method='ocr'),
        ParsedTextBlock('repeat',2,'2024年度决算表',confidence=.99,extraction_method='ocr'),
        ParsedTextBlock('n',2,'本表反映单位本年度收入。',confidence=.99,extraction_method='ocr'),
        ParsedTextBlock('nan',2,'2024年度部门决算表',confidence=float('nan'),extraction_method='ocr'),
        ParsedTextBlock('inf',2,'2024年度部门决算表',confidence=float('inf'),extraction_method='ocr'),
        ParsedTextBlock('bool',2,'2024年度部门决算表',confidence=True,extraction_method='ocr'),
        ParsedTextBlock('overflow',2,'2024年度部门决算表',confidence=1.01,extraction_method='ocr'),
        ParsedTextBlock('bad',2,'2024年度部门决算表',confidence=.97,extraction_method='ocr')]
    raw=['',''];pages,spans=qualified_identity_view(raw,SimpleNamespace(text_blocks=blocks))
    assert raw==['',''] and pages[1].count('2024年度决算表')==1
    evidence=collect(pages,source_spans=spans)
    value=asdict(resolve_material_scope(evidence=evidence,template_qualified=True))
    assert value['status']=='conflict'
    assert all(e.source_blocks[0]['source_block_id'] in ('cover','h','n') for e in evidence)
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
