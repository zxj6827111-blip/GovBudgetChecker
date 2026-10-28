import pytest
from src.services.template_scope import Evidence, HumanDecision, resolve_material_scope


def ev(scope, kind, page=2, qualified=True):
    return Evidence(scope, kind, page, f'{scope} {kind}', kind, qualified)


def resolve(*rows, **kwargs):
    return resolve_material_scope(evidence=rows, template_qualified=kwargs.pop('template_qualified', True), **kwargs)


@pytest.mark.parametrize('cover,body', [('department', 'unit'), ('unit', 'department')])
def test_both_directions_cover_conflict(cover, body):
    result = resolve(ev(cover, 'declaration', 1), ev(body, 'heading'), ev(body, 'table_note'))
    assert result.status == 'conflict' and result.value is None


def test_mixed_and_human_unknown_cannot_be_voted_away():
    rows = (ev('unit', 'heading'), ev('department', 'table_note'), ev('department', 'table_note', 3))
    assert resolve(*rows).status == 'conflict'
    result = resolve(*rows, human=HumanDecision('unknown', '1'))
    assert result.status == 'insufficient' and result.reason == 'human_unknown'


def test_human_and_cover_independence():
    rows = (ev('department', 'declaration', 1), ev('unit', 'heading'), ev('unit', 'table_note'))
    assert resolve(*rows, human=HumanDecision('unit', '2')).cover_independent_scope is None
    result = resolve(*rows, human=HumanDecision('unit', '2', True))
    assert result.status == 'confirmed' and result.cover_independent_scope == 'unit'
    assert len(result.evidence) == 3


def test_unapproved_template_is_reference_only():
    result = resolve(ev('unit', 'heading'), ev('department', 'table_note'), template_qualified=False)
    assert result.observed_template_style == 'mixed' and result.status == 'insufficient'
    result = resolve(ev('unit', 'declaration', 1), ev('department', 'heading'), template_qualified=False)
    assert result.value == 'unit' and result.cover_independent_scope is None


def test_template_requires_two_categories_and_compatible_archive():
    assert resolve(ev('unit', 'heading')).status == 'insufficient'
    rows = (ev('unit', 'heading'), ev('unit', 'table_note'))
    assert resolve(*rows).cover_independent_scope == 'unit'
    assert resolve(*rows, archive_scope='department').status == 'conflict'
    assert resolve(ev('unit', 'heading', qualified=False), ev('unit', 'table_note')).status == 'insufficient'
