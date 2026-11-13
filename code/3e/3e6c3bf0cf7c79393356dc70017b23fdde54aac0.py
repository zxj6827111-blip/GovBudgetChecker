"""IR3-GAP-01：原页字段边界由表头与完整语法证明，空隙/文本段不能授权裁切。"""
import pytest
import pymupdf as fitz

from scripts.effect_fact_sources import _AMOUNT, _UNIT, whole_source_token


@pytest.fixture
def document():
    with fitz.open() as doc:
        doc.new_page()
        yield doc


def anchor(page, text, occurrence=0):
    return {'page': 1, 'quote': text, 'bbox': list(page.search_for(text)[occurrence])}


def context(page, label):
    return {'row': anchor(page, 'All'), 'column': anchor(page, label)}


def headers(page, xs):
    page.insert_text((30, 100), 'All')
    for x, label in zip(xs, ['Total', 'Basic', 'Project'], strict=True):
        page.insert_text((x, 65), label)


@pytest.mark.parametrize('gap', [3, 8, 16])
@pytest.mark.parametrize('notation', ['-{}', '( {} )', '{} e3'])
@pytest.mark.parametrize('separate_segments', [False, True])
def test_gap01_complete_notation_survives_spaces_and_pdf_segments(document, gap, notation, separate_segments):
    p = document[0]
    headers(p, [150, 300, 450])
    for x, literal in zip([150, 300, 450], ['10.00', '6.00', '4.00'], strict=True):
        if notation == '-{}':
            parts = ['-', literal]
        elif notation == '( {} )':
            parts = ['(', literal, ')']
        else:
            parts = [literal, 'e3']
        if separate_segments:
            for part in parts:
                p.insert_text((x, 100), part, fontname='cour', fontsize=10)
                x += len(part) * 6 + gap * 3
        else:
            p.insert_text((x, 100), (' ' * gap).join(parts), fontname='cour', fontsize=10)
    # 裁窄外部引文是真实子串；必须因同格完整表示拒收，而非假引文。
    with pytest.raises(ValueError):
        whole_source_token(p, anchor(p, '10.00'), _AMOUNT, context(p, 'Total'))
    if notation == '-{}':
        left, right = p.search_for('-')[0], p.search_for('10.00')[0]
        full = {'bbox': list(left | right), 'quote': '-10.00'}
        assert whole_source_token(p, full, _AMOUNT, context(p, 'Total')) == '-10.00'


@pytest.mark.parametrize('magnitude', ['百', '千', '100'])
@pytest.mark.parametrize('separate_segments', [False, True])
def test_gap01_unit_prefix_is_not_lost_across_segments(document, magnitude, separate_segments):
    p = document[0]
    if separate_segments:
        p.insert_text((125, 35), magnitude, fontname='china-s')
        p.insert_text((220, 35), '万元', fontname='china-s')
    else:
        p.insert_text((125, 35), magnitude + '     万元', fontname='china-s')
    with pytest.raises(ValueError):
        whole_source_token(p, anchor(p, '万元'), _UNIT, {})


@pytest.mark.parametrize('values', [
    ['10.00', '6.00', '4.00'], ['-10.00', '-6.00', '-4.00'],
    ['+10.00', '+6.00', '+4.00'], ['1,000.00', '600.00', '400.00'],
])
def test_gap01_one_pdf_line_can_have_three_proven_amount_columns(document, values):
    p = document[0]
    text = (' ' * 10).join(values)
    p.insert_text((150, 100), text, fontname='cour', fontsize=10)
    xs = [150 + text.index(value) * 6 for value in values]
    headers(p, xs)
    # 必须确实测试一个原生 PDF line 内的多个字段，不能仅靠分成多行通过。
    assert any(line['bbox'][1] > 80 and len(line['spans'][0]['text']) == len(text)
               for block in p.get_text('dict')['blocks'] for line in block.get('lines', []))
    for label, value in zip(['Total', 'Basic', 'Project'], values, strict=True):
        assert whole_source_token(p, anchor(p, value), _AMOUNT, context(p, label)) == value


@pytest.mark.parametrize('fault', ['missing_header', 'same_column', 'unparsed_fragment', 'crossing_headers'])
def test_gap01_ambiguous_field_partition_is_rejected(document, fault):
    p = document[0]
    p.insert_text((30, 100), 'All')
    p.insert_text((150, 65), 'Total')
    p.insert_text((150, 100), '10.00')
    if fault == 'missing_header':
        p.insert_text((300, 100), '6.00')
    elif fault == 'same_column':
        p.insert_text((175, 100), '6.00', fontsize=8)
    elif fault == 'unparsed_fragment':
        p.insert_text((120, 100), 'unknown')
    else:
        p.insert_text((140, 65), 'X')
        p.insert_text((140, 100), '-')
    with pytest.raises(ValueError):
        whole_source_token(p, anchor(p, '10.00'), _AMOUNT, context(p, 'Total'))
