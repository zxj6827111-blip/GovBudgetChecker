import pytest
from dataclasses import asdict
from src.services.template_scope import (Evidence, HumanDecision, comprehensive_writing_contrast,
                                         resolve_material_scope)


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


def test_invalid_human_decision_is_rejected():
    with pytest.raises(ValueError):
        resolve(human=HumanDecision('bogus', '3'))


# ---------------------------------------------------------------------------
# 阶段 0：所选层级作为检查基准（source=archive）
# ---------------------------------------------------------------------------


def test_selected_level_confirms_as_archive_and_checks_cover():
    # 无人工写法决定时，所选层级直接成为检查基准，并可用于核对封面。
    result = resolve(selected_level='unit')
    confirmed = resolve(selected_level='department')
    assert result.status == 'confirmed' and result.value == 'unit'
    assert result.source == 'archive' and result.reason == 'upload_selection_level'
    assert result.cover_independent_scope == 'unit'
    assert confirmed.value == 'department' and confirmed.source == 'archive'
    assert confirmed.cover_independent_scope == 'department'


def test_selected_level_beats_declaration_but_not_human():
    rows = (ev('department', 'declaration', 1),)
    result = resolve(*rows, selected_level='unit', template_qualified=False)
    assert result.status == 'confirmed' and result.source == 'archive' and result.value == 'unit'
    human = resolve(*rows, selected_level='unit', template_qualified=False, human=HumanDecision('department', '7'))
    assert human.source == 'human' and human.value == 'department'
    # 人工“暂不确定”是明确的人工判断：保持依据不足，不退回所选层级。
    unknown = resolve(*rows, selected_level='unit', template_qualified=False, human=HumanDecision('unknown', '8'))
    assert unknown.status == 'insufficient' and unknown.reason == 'human_unknown'


@pytest.mark.parametrize('level', ['city', 'district', 'government', 'unknown', '', None])
def test_unusable_selected_level_keeps_original_path(level):
    result = resolve(selected_level=level)
    assert result.status == 'insufficient' and result.source == 'unresolved'
    assert result.cover_independent_scope is None
    declared = resolve(ev('unit', 'declaration', 1), selected_level=2, template_qualified=False)
    assert declared.source == 'declaration' and declared.value == 'unit'
    assert result.source == 'unresolved'  # 非法层级不使用：原路径不变


def test_invalid_archive_level_still_rejected():
    # archive_scope 的非法值仍按原契约拒绝（与 selected_level 的宽松处理不同）。
    with pytest.raises(ValueError):
        resolve(selected_level='unit', archive_scope='bogus')


# ---------------------------------------------------------------------------
# 阶段 0：“写法全面相反”判定
# ---------------------------------------------------------------------------


def native(page, text):
    return {'page': page, 'start': 0, 'end': len(text), 'source_method': 'native_text'}


def ocr(page, text, start=0):
    return {'page': page, 'start': start, 'end': start + len(text), 'source_method': 'ocr'}


def contrast(rows, pages, selected='unit', **kwargs):
    return comprehensive_writing_contrast(
        evidence=[asdict(r) if isinstance(r, Evidence) else r for r in rows],
        selected_scope=selected, page_texts=pages, **kwargs)


def opp(scope, kind, page, text, blocks):
    return {'scope': scope, 'kind': kind, 'page': page, 'text': text,
            'qualified': True, 'start': 0, 'end': len(text), 'source_blocks': tuple(blocks)}


TOC_PAGE = '目录\n一、单位决算表\n二、部门决算情况说明\n'
BODY_UNIT_HEADING = '2024年度单位决算表\n正文内容。'


def test_comprehensive_contrast_requires_heading_and_table_note():
    pages = ['2024年度单位决算', '2024年度部门决算表\n本表反映部门本年度收入。']
    rows = [opp('department', 'heading', 2, '2024年度部门决算表', [native(2, pages[1])]),
            opp('department', 'table_note', 2, '本表反映正文收入。', [native(2, pages[1])])]
    assert contrast(rows, pages)['comprehensive_contrast'] is True
    # 缺表注：只有标题指向另一侧，不算全面相反。
    assert contrast(rows[:1], pages)['comprehensive_contrast'] is False
    # 缺标题：只有表注指向另一侧，不算全面相反。
    assert contrast(rows[1:], pages)['comprehensive_contrast'] is False


def test_comprehensive_contrast_rejected_on_mixed_or_selected_hits():
    pages = ['2024年度单位决算', '2024年度部门决算表\n本表反映部门本年度收入。', BODY_UNIT_HEADING]
    rows = [opp('department', 'heading', 2, '2024年度待核对决算表', [native(2, pages[1])]),
            opp('department', 'table_note', 2, '本表反映部门本年度收入。', [native(2, pages[1])]),
            opp('unit', 'heading', 3, '2024年度单位决算表', [native(3, pages[2])])]
    summary = contrast(rows, pages)
    assert summary['comprehensive_contrast'] is False and len(summary['selected_hits']) == 1


def test_comprehensive_contrast_excludes_ocr_and_unknown_sources():
    pages = ['2024年度单位决算', '2024年度部门决算表\n本表反映部门本年度收入。']
    heading = opp('department', 'heading', 2, '2024年度部门决算表', [ocr(2, '2024年度部门决算表')])
    note = opp('department', 'table_note', 2, '本表反映部门本年度收入。', [native(2, pages[1])])
    # 高置信度 OCR 块也一律不计。
    assert contrast([heading, note], pages)['comprehensive_contrast'] is False
    # 来源块为空（无法证明来自原生文本）不计。
    unproven = opp('department', 'heading', 2, '2024年度部门决算表', [])
    assert contrast([unproven, note], pages)['comprehensive_contrast'] is False


def test_comprehensive_contrast_ignores_toc_pages_and_toc_lines():
    pages = ['2024年度单位决算', TOC_PAGE, '2024年度部门决算表\n本表反映部门本年度收入。']
    toc_heading = opp('department', 'heading', 2, '部门决算情况说明', [native(2, TOC_PAGE)]
                      )
    note = opp('department', 'table_note', 3, '本表反映部门本年度收入。', [native(3, pages[2])])
    # 只有目录页写法相反、正文一致：不判为全面相反。
    assert contrast([toc_heading], pages)['comprehensive_contrast'] is False
    assert any(e['reason'] == 'toc_page' for e in contrast([toc_heading], pages)['excluded'])
    # 目录式行（点线引导或页码尾巴）不计。
    dotted_page = '一、部门决算情况说明……12\n其他内容\n'
    dotted = opp('department', 'heading', 2, '部门决算情况说明', [native(2, dotted_page)])
    summary_dotted = contrast([dotted], ['2024年度单位决算', dotted_page])
    assert summary_dotted['excluded'][0]['reason'] == 'toc_style_line'
    tail_page = '一、部门决算情况说明  12\n'
    tailed = opp('department', 'heading', 2, '部门决算情况说明', [native(2, tail_page)])
    summary_tail = contrast([tailed], ['2024年度单位决算', tail_page])
    assert summary_tail['excluded'][0]['reason'] == 'toc_style_line'


def test_comprehensive_contrast_keeps_real_body_headings():
    pages = ['2024年度单位决算', '第二部分\n2024年度部门决算表\n本表反映部门本年度收入。\n市级部门决算情况另行说明。']
    rows = [opp('department', 'heading', 2, '2024年度部门决算表', [native(2, pages[1])]),
            opp('department', 'table_note', 2, '本表反映部门本年度收入。', [native(2, pages[1])])]
    summary = contrast(rows, pages)
    assert summary['comprehensive_contrast'] is True and summary['opposite']['heading']
    # 封面（第 1 页）证据不参与。
    cover_rows = [opp('unit', 'heading', 1, '单位决算', [native(1, pages[0])])]
    assert all(e['reason'] == 'cover_page' for e in contrast(cover_rows, pages)['excluded'])
