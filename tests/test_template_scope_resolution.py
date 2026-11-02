import pytest
from dataclasses import asdict
from src.services.template_scope import (Evidence, HumanDecision, comprehensive_writing_contrast,
                                         cover_matches_selected_org, resolve_material_scope)


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
# 阶段 0 审核修正：封面主体与所选组织一致性
# ---------------------------------------------------------------------------


def test_cover_match_rules():
    # 一致
    assert cover_matches_selected_org('上海市信访办公室', '上海市信访办公室') is True
    assert cover_matches_selected_org('上海市 信访办公 室', '上海市信访办公室') is True
    # 全角/半角括号视为同名（NFKC 归一）
    assert cover_matches_selected_org('上海市水务局(上海市海洋局)', '上海市水务局（上海市海洋局）') is True
    assert cover_matches_selected_org('中国(上海)自由贸易试验区临港新片区管理委员会',
                                      '中国（上海）自由贸易试验区临港新片区管理委员会') is True
    # 目录名多“单位”后缀（verified_cover_alias 同规则：目录名 == 封面名 + 单位）
    assert cover_matches_selected_org('上海市信访办公室', '上海市信访办公室单位') is True
    # 本级 /（本级）/ 本部 标注
    assert cover_matches_selected_org('上海市发展和改革委员会本级', '上海市发展和改革委员会') is True
    assert cover_matches_selected_org('上海市发展和改革委员会（本级）', '上海市发展和改革委员会') is True
    assert cover_matches_selected_org('上海市发展和改革委员会', '上海市发展和改革委员会本部') is True
    # 封面名前的附件/序号编号
    assert cover_matches_selected_org('附件1上海市农业农村委员会', '上海市农业农村委员会') is True
    assert cover_matches_selected_org('1上海市农业农村委员会', '上海市农业农村委员会') is True
    # 下属单位：封面主体是所选组织名加后缀 → 不一致
    assert cover_matches_selected_org('上海市文化和旅游局执法总队', '上海市文化和旅游局') is False
    # 名称完全不同 → 不一致
    assert cover_matches_selected_org('上海市财政局', '上海市数据局') is False
    # 封面名或目录名识别不到 → 不判定（维持现行为）
    assert cover_matches_selected_org(None, '上海市文化和旅游局') is None
    assert cover_matches_selected_org('上海市文化和旅游局', '') is None
    assert cover_matches_selected_org('测试局', '上海市文化和旅游局') is None


def test_selected_level_rejected_on_cover_subject_mismatch():
    rows = (ev('department', 'heading'), ev('department', 'table_note'))
    # 下属单位：不用所选层级、走原路径（模板未批准 → 依据不足），并记录原因。
    mismatch = resolve(*rows, template_qualified=False, selected_level='department',
                       cover_name='上海市文化和旅游局执法总队', selected_org_name='上海市文化和旅游局')
    assert mismatch.status == 'insufficient' and mismatch.source == 'unresolved'
    assert mismatch.value is None and mismatch.cover_independent_scope is None
    assert mismatch.archive_subject_mismatch is True
    # 一致时照常使用所选层级。
    matched = resolve(*rows, template_qualified=False, selected_level='department',
                      cover_name='上海市文化和旅游局', selected_org_name='上海市文化和旅游局')
    assert matched.source == 'archive' and matched.value == 'department'
    assert matched.archive_subject_mismatch is False
    # 封面名识别不到：维持现行为，仍使用所选层级。
    unknown = resolve(*rows, template_qualified=False, selected_level='department',
                      selected_org_name='上海市文化和旅游局')
    assert unknown.source == 'archive' and unknown.archive_subject_mismatch is False
    # 允许差异（单位后缀/本级/附件编号）不触发拒绝。
    for cover in ('上海市文化和旅游局', '上海市文化和旅游局本级',
                  '附件2上海市文化和旅游局'):
        basis = resolve(*rows, template_qualified=False, selected_level='department',
                        cover_name=cover, selected_org_name='上海市文化和旅游局单位')
        assert basis.source == 'archive' and basis.archive_subject_mismatch is False, cover
    # 人工决定优先：不做一致性拒绝，也不记 mismatch。
    human = resolve(*rows, template_qualified=False, human=HumanDecision('unit', '9'),
                    cover_name='上海市文化和旅游局执法总队', selected_org_name='上海市文化和旅游局')
    assert human.source == 'human' and human.archive_subject_mismatch is False


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
    opp('department', 'table_note', 3, '本表反映部门本年度收入。', [native(3, pages[2])])
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


def test_single_space_toc_entry_is_not_body_evidence():
    # 审核修正（P2）：目录续页条目“标题 页码”之间只有一个空格，也不是正文证据。
    pages = ['2024年度单位决算',
             '第二部分 测试中心2024 年度单位决算表 3',
             '2024 年度部门决算表\n本表反映部门本年度收入。',
             '2024 年度部门决算情况说明\n本部门经费情况见相关说明。']
    from src.services.material_writing import collect
    spans = [{'page': p, 'start': 0, 'end': len(t), 'source_method': 'native_text'}
             for p, t in enumerate(pages, 1)]
    evidence = collect(pages, (), 'final', spans)
    summary = contrast(evidence, pages)
    toc_excluded = [e for e in summary['excluded'] if e.get('reason') == 'toc_style_line']
    assert toc_excluded and toc_excluded[0]['page'] == 2
    # 排除目录条目后，正文标题与表注全面相反成立。
    assert summary['comprehensive_contrast'] is True
    # 对照：真实正文标题（无页码尾巴）不受影响。
    body_pages = ['2024年度单位决算', '第二部分\n2024 年度单位决算表\n本表反映单位本年度收入。']
    body_evidence = collect(body_pages, (), 'final',
                            [{'page': p, 'start': 0, 'end': len(t), 'source_method': 'native_text'}
                             for p, t in enumerate(body_pages, 1)])
    body_summary = contrast(body_evidence, body_pages)
    assert body_summary['selected_hits'] and not body_summary['comprehensive_contrast']


def test_wrapped_table_note_with_footer_page_number_still_counts():
    # 审核第三轮（P2）：无句号表注跨软换行把下一行页脚页码吃进证据时，
    # 不得把正文表注与页脚拼成目录条目而排除这条有效表注。
    pages = ['2024年度单位决算',
             '2024 年度部门决算表\n本表反映部门本年度收入情况\n12\n',
             '2024 年度部门决算情况说明\n本表反映部门本年度支出情况\n34\n']
    from src.services.material_writing import collect
    spans = [{'page': p, 'start': 0, 'end': len(t), 'source_method': 'native_text'}
             for p, t in enumerate(pages, 1)]
    evidence = collect(pages, (), 'final', spans)
    notes = [asdict(e) for e in evidence if e.kind == 'table_note']
    # 前置确认：去空白匹配确实让表注证据跨行吃到了页脚页码。
    assert len(notes) == 2
    assert notes[0]['text'].endswith('12') and notes[1]['text'].endswith('34')
    summary = contrast(evidence, pages)
    assert not [e for e in summary['excluded'] if e.get('reason') == 'toc_style_line']
    assert len(summary['opposite']['table_note']) == 2
    assert summary['comprehensive_contrast'] is True


@pytest.mark.parametrize('toc', ['2024年度单位决算情况说明 12',
                                 '2024年度单位决算情况\n说明 12'],
                         ids=['single_line', 'wrapped_title'])
def test_wrapped_toc_title_still_excluded(toc):
    # 审核第四轮（P2）：目录标题折行、页码在末行时同样要排除；
    # 表注/声明仍只看起始物理行（上一轮页脚回归保持不变）。
    pages = ['2024年度单位决算', toc,
             '2024 年度部门决算表\n本表反映部门本年度收入。',
             '2024 年度部门决算情况说明\n本表反映部门本年度支出。']
    from src.services.material_writing import collect
    spans = [{'page': p, 'start': 0, 'end': len(t), 'source_method': 'native_text'}
             for p, t in enumerate(pages, 1)]
    evidence = collect(pages, (), 'final', spans)
    summary = contrast(evidence, pages)
    toc_excluded = [e for e in summary['excluded'] if e.get('reason') == 'toc_style_line']
    assert toc_excluded and toc_excluded[0]['page'] == 2, toc
    assert summary['comprehensive_contrast'] is True


def test_comprehensive_contrast_keeps_real_body_headings():
    pages = ['2024年度单位决算', '第二部分\n2024年度部门决算表\n本表反映部门本年度收入。\n市级部门决算情况另行说明。']
    rows = [opp('department', 'heading', 2, '2024年度部门决算表', [native(2, pages[1])]),
            opp('department', 'table_note', 2, '本表反映部门本年度收入。', [native(2, pages[1])])]
    summary = contrast(rows, pages)
    assert summary['comprehensive_contrast'] is True and summary['opposite']['heading']
    # 封面（第 1 页）证据不参与。
    cover_rows = [opp('unit', 'heading', 1, '单位决算', [native(1, pages[0])])]
    assert all(e['reason'] == 'cover_page' for e in contrast(cover_rows, pages)['excluded'])
