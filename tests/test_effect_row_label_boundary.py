"""IR3-GAP-02：先核原页结构，真实子串与一致摘要不能授权删除金额字形。"""
import pytest
import pymupdf as fitz

from scripts.effect_fact_sources import _AMOUNT, whole_source_token


@pytest.fixture
def document():
    with fitz.open() as doc:
        doc.new_page()
        yield doc


def anchor(page, quote, occurrence=0):
    boxes = page.search_for(quote)
    if quote == 'Total':
        boxes = [box for box in boxes if box.y1 < 80]
    return {'page': 1, 'quote': quote, 'bbox': list(boxes[occurrence])}


def grid(page, label='All', signed_column=0, prefix='-', prefix_x=None):
    page.insert_text((45, 100), label)
    for i, (x, heading, value) in enumerate(zip(
        [150, 250, 350, 450], ['Total', 'Basic', 'Project', 'Reserve'],
        ['10.00', '6.00', '4.00', '6.00'], strict=True
    )):
        page.insert_text((x, 65), heading)
        if i == signed_column:
            page.insert_text((prefix_x if prefix_x is not None else x, 100), prefix)
            page.insert_text((x + 10, 100), value)
        else:
            page.insert_text((x, 100), value)


def broad_row(page, label, prefix, occurrence=0):
    box = page.search_for(label)[0] | page.search_for(prefix)[occurrence]
    return {'page': 1, 'bbox': list(box), 'quote': page.get_textbox(box)}


@pytest.mark.parametrize('column', [0, 1, 2, 3])
@pytest.mark.parametrize('prefix', ['-', '+', '('])
def test_row_mask_cannot_enter_any_amount_column(document, column, prefix):
    p = document[0]
    grid(p, signed_column=column, prefix=prefix)
    row = broad_row(p, 'All', prefix)
    # 即使被检查操作数是另一列，也不能删除任何相关金额列的字符。
    ctx = {'row': row, 'column': anchor(p, 'Total')}
    with pytest.raises(ValueError, match='行标范围侵入原页金额列'):
        whole_source_token(p, anchor(p, '10.00'), _AMOUNT, ctx)


def test_sign_before_header_still_belongs_to_complete_amount(document):
    p = document[0]
    grid(p, prefix_x=140)
    row = broad_row(p, 'All', '-')
    assert row['bbox'][2] < p.search_for('Total')[0].x0
    # 符号在列头左侧也不能仅靠无投影交叠就被删掉。
    with pytest.raises(ValueError, match='行标消除会改变完整金额表示'):
        whole_source_token(p, anchor(p, '10.00'), _AMOUNT,
                           {'row': row, 'column': anchor(p, 'Total')})


@pytest.mark.parametrize('label', ['All -', 'A-01', 'Group (2)', 'Total:'])
def test_legitimate_identity_punctuation_is_preserved(document, label):
    p = document[0]
    grid(p, label=label, signed_column=None)
    assert whole_source_token(p, anchor(p, '10.00'), _AMOUNT,
                              {'row': anchor(p, label), 'column': anchor(p, 'Total')}) == '10.00'


def test_complete_negative_with_independent_hyphenated_row_is_legal(document):
    p = document[0]
    grid(p, label='All -')
    sign = p.search_for('-')[1]
    full = {'bbox': list(sign | p.search_for('10.00')[0]), 'quote': '- 10.00'}
    assert whole_source_token(p, full, _AMOUNT,
                              {'row': anchor(p, 'All -'), 'column': anchor(p, 'Total')}) == '-10.00'


def test_simple_label_and_amount_can_share_native_pdf_line(document):
    p = document[0]
    p.insert_text((45, 100), 'All' + ' ' * 14 + '10.00', fontname='cour', fontsize=10)
    p.insert_text((147, 65), 'Total')
    assert whole_source_token(p, anchor(p, '10.00'), _AMOUNT,
                              {'row': anchor(p, 'All'), 'column': anchor(p, 'Total')}) == '10.00'


def test_row_cannot_cut_prefix_out_of_shared_native_line(document):
    p = document[0]
    p.insert_text((45, 100), 'All' + ' ' * 12 + '-  10.00', fontname='cour', fontsize=10)
    p.insert_text((150, 65), 'Total')
    row = broad_row(p, 'All', '-')
    assert row['bbox'][2] < p.search_for('Total')[0].x0
    with pytest.raises(ValueError, match='行标消除会改变完整金额表示'):
        whole_source_token(p, anchor(p, '10.00'), _AMOUNT,
                           {'row': row, 'column': anchor(p, 'Total')})


def test_identity_column_header_is_not_an_amount_column(document):
    p = document[0]
    grid(p, label='All -', signed_column=None)
    p.insert_text((45, 65), 'Name')
    assert whole_source_token(p, anchor(p, '10.00'), _AMOUNT,
                              {'row': anchor(p, 'All -'), 'column': anchor(p, 'Total')}) == '10.00'


def ruled_grid(page, emission):
    """明确表格线的版式：身份列右界130，Total格130–230，列头文字从150起。"""
    page.insert_text((45, 35), 'Fiscal table')
    page.insert_text((125, 35), 'wan')
    for x in [35, 130, 230, 330, 430, 520]:
        page.draw_line((x, 50), (x, 112), width=.5)
    for y in [50, 76, 112]:
        page.draw_line((35, y), (520, y), width=.5)
    for x, heading in zip([150, 250, 350, 450], ['Total', 'Basic', 'Project', 'Reserve'], strict=True):
        page.insert_text((x, 65), heading)
    if emission == 'joined':
        page.insert_text((45, 100), 'All' + ' ' * 13 + '-', fontname='cour', fontsize=10)
    elif emission == 'split':
        page.insert_text((45, 100), 'All', fontname='cour', fontsize=10)
        page.insert_text((141, 100), '-', fontname='cour', fontsize=10)
    else:
        page.insert_text((45, 100), 'All -', fontname='cour', fontsize=10)
    for x, value in zip([155, 250, 350, 450], ['10.00', '6.00', '4.00', '6.00'], strict=True):
        page.insert_text((x, 100), value, fontname='cour', fontsize=10)


def lines_of(page):
    return [''.join(s['text'] for s in line['spans'])
            for block in page.get_text('dict')['blocks'] for line in block.get('lines', [])]


def test_drawn_cell_boundary_rules_instead_of_text_segmentation(document):
    # 同一可视表格，仅 All 与负号的 native line 分段不同；分段与像素都
    # 不改变结论：负号位于画出金额格内时必须一致拒收。
    verdicts = []
    for emission in ('joined', 'split'):
        p = document[0] if emission == 'joined' else document.new_page()
        ruled_grid(p, emission)
        row = {'page': 1, 'quote': 'All -',
               'bbox': list(p.search_for('All')[0] | p.search_for('-')[0])}
        assert p.search_for('All')[0].x1 < 130 < p.search_for('-')[0].x0
        with pytest.raises(ValueError, match='行标消除会改变完整金额表示'):
            whole_source_token(p, anchor(p, '10.00'), _AMOUNT,
                               {'row': row, 'column': anchor(p, 'Total')})
        verdicts.append('reject')
    assert verdicts == ['reject', 'reject']


def test_drawn_boundary_separating_label_from_amount_allows_hyphen(document):
    p = document[0]
    ruled_grid(p, 'legal')
    # 横线属于身份列内的合法行标：画出分界130位于行标与金额字形之间。
    assert p.search_for('-')[0].x1 < 130 < p.search_for('10.00')[0].x0
    assert whole_source_token(p, anchor(p, '10.00'), _AMOUNT,
                              {'row': anchor(p, 'All -'), 'column': anchor(p, 'Total')}) == '10.00'


def test_mask_under_header_x0_still_rejected_by_amount_component(document):
    p = document[0]
    p.insert_text((45, 100), 'All')
    p.insert_text((145, 100), '- 10.00')
    for x, heading, value in zip([150, 250, 350, 450], ['Total', 'Basic', 'Project', 'Reserve'],
                                 ['10.00', '6.00', '4.00', '6.00'], strict=True):
        p.insert_text((x, 65), heading)
        if x != 150:
            p.insert_text((x, 100), value)
    row = {'page': 1, 'quote': 'All -',
           'bbox': list(p.search_for('All')[0] | p.search_for('-')[0])}
    assert row['bbox'][2] < p.search_for('Total')[0].x0
    # 行标未越过列头文字也无效：负号属于金额自身文字组件'- 10.00'。
    with pytest.raises(ValueError, match='行标消除会改变完整金额表示'):
        whole_source_token(p, anchor(p, '10.00'), _AMOUNT,
                           {'row': row, 'column': anchor(p, 'Total')})


def test_without_drawn_structure_label_component_membership_decides(document):
    p = document[0]
    p.insert_text((45, 100), 'All' + ' ' * 13 + '-', fontname='cour', fontsize=10)
    p.insert_text((147, 65), 'Total')
    p.insert_text((155, 100), '10.00', fontname='cour', fontsize=10)
    p.insert_text((250, 65), 'Basic')
    p.insert_text((250, 100), '6.00', fontname='cour', fontsize=10)
    p.insert_text((350, 65), 'Project')
    p.insert_text((350, 100), '4.00', fontname='cour', fontsize=10)
    p.insert_text((450, 65), 'Reserve')
    p.insert_text((450, 100), '6.00', fontname='cour', fontsize=10)
    assert not p.get_drawings()
    row = {'page': 1, 'quote': 'All -',
           'bbox': list(p.search_for('All')[0] | p.search_for('-')[0])}
    # 无画出分界结构时，负号属于已逐字验证的行标文字组件才可删；
    # 这是封存合法横线行标正控（legitimate_hyphen_row_label_positive）
    # 所要求的语义，负号未与金额字形同组件即拒收（见上一测试）。
    assert whole_source_token(p, anchor(p, '10.00'), _AMOUNT,
                              {'row': row, 'column': anchor(p, 'Total')}) == '10.00'
