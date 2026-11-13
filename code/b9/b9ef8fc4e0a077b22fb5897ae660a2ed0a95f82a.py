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
    return {'page': 1, 'quote': quote, 'bbox': list(page.search_for(quote)[occurrence])}


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
