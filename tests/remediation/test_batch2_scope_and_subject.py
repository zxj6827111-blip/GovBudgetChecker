from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.engine.native_narrative_checks import _foreign_owner
from src.engine.rule_identity import required_identity_dimensions, rule_identity_requirements
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import R33236_DocumentScopeTerminology
from src.schemas.parse_artifact import ParseArtifact
from src.services.check_subject import resolve_check_subject
from src.services.document_profile_resolver import resolve_document_profile
from src.services.document_scope import apply_profile, source_conflicts
from src.services.fiscal_source_scope import organization_scope
from src.services.input_processing_identity import bind_artifact_document_identity


def test_verified_directory_suffix_restores_native_owner_without_adopting_other_organizations():
    name = '上海市信访办公室'
    values = dict(organization_id='unit-node', organization_name=name+'单位', organization_level='unit',
                  cover_name=name, cover_text=name+'2024年度决算')
    subject = resolve_check_subject(**values)
    assert subject['organization_name'] == name+'单位'
    assert subject['evidence'][0]['page'] == 1
    assert not _foreign_owner(name+'2024年度财政拨款收入支出总计31730.89万元', subject['name'])
    for changes in [dict(cover_text=None), dict(cover_conflicted=True), dict(organization_name='上海市信访办公室下属中心'),
                    dict(organization_name='其他单位'), dict(organization_level='department'), dict(cover_name='上海市财政局')]:
        result = resolve_check_subject(**{**values, **changes})
        assert result['source'] != 'verified_cover_alias'
    assert _foreign_owner('上海市财政局2024年度财政拨款收入支出总计100万元', subject['name'])


@pytest.mark.parametrize('rule_id', ['CMM-005','CMM-006','CMM-007','V33-234','V33-245'])
def test_only_material_scope_requirement_is_removed(rule_id):
    dimensions = required_identity_dimensions(rule_id)
    assert 'org_scope' not in dimensions
    assert set(dimensions) == {'organization','year','fund_scope','budget_stage','instance_uid'}


def test_scope_sensitive_check_is_still_deferred_and_conditional_rule_only_blocks_actual_expression():
    context = SimpleNamespace(org_scope=None, facts=[SimpleNamespace(extra_dims={'org_scope_pending':True})])
    doc = SimpleNamespace(page_texts=['2024年度决算','第三部分年度决算情况说明'],fact_context=context)
    with pytest.raises(RuleDeferred):
        with rule_identity_requirements(doc,'CMM-018'): pass
    with rule_identity_requirements(doc,'V33-236'):
        assert R33236_DocumentScopeTerminology().apply(doc) == []
    doc.page_texts.append('部门决算安排10万元')
    with pytest.raises(RuleDeferred):
        with rule_identity_requirements(doc,'V33-236'): R33236_DocumentScopeTerminology().apply(doc)


def decision(scope='unit_self'):
    return dict(request_id='00000000-0000-0000-0000-000000000001',file_hash='a'*64,material_scope=scope,
                revision=1,actor='reviewer',note='核对原始编报说明',evidence_reference='第3页机构设置')


def test_confirmation_changes_processing_identity_and_not_source_content_or_other_dimensions():
    artifact=ParseArtifact(source_file_hash='a'*64)
    original=deepcopy(artifact)
    bind_artifact_document_identity(artifact,scope_confirmation=decision())
    first=artifact.to_dict()
    assert organization_scope(artifact)=='unit'
    assert artifact.source_file_hash==original.source_file_hash and artifact.table_fragments==original.table_fragments
    bind_artifact_document_identity(artifact,scope_confirmation={**decision(),'dispatch_status':'applied'})
    assert artifact.to_dict()==first
    before=artifact.parse_run_uid
    bind_artifact_document_identity(artifact,scope_confirmation=decision('unknown'))
    assert artifact.parse_run_uid!=before and organization_scope(artifact) is None
    artifact.source_file_hash='b'*64
    assert organization_scope(artifact) is None
    profile=resolve_document_profile(page_texts=['2024年度决算'],filename='x.pdf')
    apply_profile(profile,decision())
    assert profile.level=='unit' and profile.subject_level.source=='human_confirmation'
    assert profile.caliber.value is None


def test_conflicting_original_scope_requires_explicit_adjudication():
    evidence=[dict(dimension='subject_level',value='department',decisive=True)]
    assert source_conflicts(evidence,'unit_self')==evidence
    assert not source_conflicts(evidence,'department_summary')
    assert not source_conflicts(evidence,'unknown')
