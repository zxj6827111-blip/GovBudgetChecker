"""改部门/单位关联后，运行身份隔离；原件及原始格不变。"""
from copy import deepcopy

from src.schemas.parse_artifact import ParseArtifact
from src.services.input_processing_identity import bind_artifact_document_identity


def test_same_pdf_reassociated_to_unit_has_a_distinct_immutable_run():
    original = ParseArtifact(source_file_hash='a' * 64, parse_run_uid='source-run')
    department, unit = deepcopy(original), deepcopy(original)
    common = {'fiscal_year': 2024, 'report_kind': 'final', 'doc_type': 'dept_final'}
    bind_artifact_document_identity(department, organization_id='dept', organization_name='信访办公室', **common)
    bind_artifact_document_identity(unit, organization_id='unit', organization_name='信访办公室', **common)
    assert department.parse_run_uid != unit.parse_run_uid
    assert department.config_fingerprint != unit.config_fingerprint
    assert department.source_file_hash == unit.source_file_hash == original.source_file_hash
    assert department.table_fragments == unit.table_fragments == original.table_fragments
    assert original.parse_run_uid == 'source-run' and 'document_binding' not in original.processing_manifest
    assert department.processing_manifest['document_binding']['organization_id'] == 'dept'
    assert unit.processing_manifest['document_binding']['organization_id'] == 'unit'
    once = unit.to_dict()
    bind_artifact_document_identity(unit, organization_id='unit', organization_name='信访办公室', **common)
    assert unit.to_dict() == once


def test_corrected_year_and_checked_subject_cannot_reuse_old_fact_run():
    base = ParseArtifact(source_file_hash='a' * 64)
    variants = []
    for year, subject in [(2024, '信访办公室'), ('2024', '信访办公室'), (2023, '信访办公室'), (2024, '信访办公室单位')]:
        artifact = deepcopy(base)
        bind_artifact_document_identity(artifact, organization_id='unit', fiscal_year=year, check_subject_name=subject)
        variants.append(artifact.parse_run_uid)
    assert variants[0] == variants[1]
    assert len(set(variants)) == 3


def test_bound_directory_name_is_not_stripped_as_report_filename():
    from pathlib import Path
    from src.services.structured_ingest_runner import _normalize_org_name
    assert _normalize_org_name('上海市信访办公室单位', Path('2024年决算.pdf'), preserve_identity=True) == '上海市信访办公室单位'
