from pathlib import Path
from unittest.mock import patch
import pytest
from src.services.disclosure_template_import import compare_text, import_source


def test_import_preserves_original_and_does_not_overwrite(tmp_path):
    source = tmp_path / 'a.doc'
    source.write_bytes(b'original')
    with patch('src.services.disclosure_template_import._extract', return_value=('2024年度\n单位', 'test')):
        result = import_source(source, tmp_path / 'draft', {'year': 2024})
    assert result['status'] == 'draft'
    assert result['extraction_status'] == 'needs_visual_verification'
    assert (tmp_path / 'draft/original.doc').read_bytes() == b'original'
    with pytest.raises(FileExistsError):
        import_source(source, tmp_path / 'draft', {})


def test_conversion_failure_keeps_original_and_error(tmp_path):
    source = tmp_path / 'bad.doc'
    source.write_bytes(b'invalid')
    with patch('src.services.disclosure_template_import._extract', side_effect=ValueError('bad conversion')):
        result = import_source(source, tmp_path / 'draft', {})
    assert result['extraction_status'] == 'failed'
    assert 'bad conversion' in result['error']
    assert (tmp_path / 'draft/original.doc').read_bytes() == b'invalid'
    assert not (tmp_path / 'draft/extracted.txt').exists()


def test_differences_retain_original_and_position_never_auto_approve():
    changes = compare_text('封面\n2024年度决算\n金额单位：万元\n', '封面\n2025年度决算\n金额单位：元\n')
    assert changes and all(x['business_classification'] == 'pending_review' for x in changes)
    assert '2024' in str(changes) and '2025' in str(changes)
    assert '万元' in str(changes) and '金额单位：元' in str(changes)
    assert changes[0]['old'][0]['offset'] == 3
    assert changes[0]['old'][0]['page'] is None
    assert changes[0]['old'][0]['paragraph'] == 2


def test_unsupported_or_empty_is_rejected_before_creating_draft(tmp_path):
    bad = tmp_path / 'x.wps'
    bad.write_bytes(b'x')
    with pytest.raises(ValueError): import_source(bad, tmp_path / 'draft', {})
    assert not (tmp_path / 'draft').exists()


def test_corrupt_xls_keeps_original_and_failed_receipt(tmp_path):
    source=tmp_path/'broken.xls'
    source.write_bytes(b'not-a-valid-xls')
    target=tmp_path/'retained'
    result=import_source(source,target,{'report_kind':'budget','source_report_year':None})
    assert result['extraction_status']=='failed' and result['error']
    assert (target/'original.xls').read_bytes()==source.read_bytes()
    assert (target/'manifest.json').exists() and not (target/'extracted.txt').exists()
