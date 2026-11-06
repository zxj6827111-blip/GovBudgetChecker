"""独立事实的原格、表头、行标和单位必须形成同一空间证据链。"""
from decimal import Decimal, InvalidOperation
import re
import unicodedata

from scripts.acceptance_artifacts import digest

FACT_SOURCE_VERSION = 'independent-fiscal-cell-v2'
FACTORS = {'元': Decimal('.0001'), '万元': Decimal('1'), '亿元': Decimal('10000'), 'wan': Decimal('1'), 'yuan': Decimal('.0001')}



_AMOUNT = re.compile(r'[+\-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?')
_UNIT = re.compile(r'亿元|万元|元|(?<![A-Za-z])(?:yuan|wan)(?![A-Za-z])')
# 识别完整原文表示与支持其语义是两步；不能先只匹配支持的子串。
_AMOUNT_SURFACE = re.compile(r'[+\-−(（\[{$￥¥]*\d[\d,.]*(?:[eE][+\-−]*\d*)?[)）\]}%％]*')
_UNIT_SURFACE = re.compile(r'[0-9.,零〇一二三四五六七八九十百千万亿兆壹贰叁肆伍陆柒捌玖拾佰仟萬億]+元|元|[A-Za-z]+')


def _norm(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value))).replace('−', '-')


def whole_source_token(page, anchor, pattern):
    """用原页字符重建完整词元，再检验框；不能把负号或“万”裁掉。"""
    box = anchor['bbox']
    observed = []
    for block in page.get_text('rawdict')['blocks']:
        for line in block.get('lines', []):
            chars = [c for span in line.get('spans', []) for c in span.get('chars', []) if not c['c'].isspace()]
            # 同一PDF行可有多个金额列。只在真实列间空隙切分，不能在负号、
            # 括号、指数或数量级前缀处切掉不支持的语法。
            runs = []
            for char in chars:
                if runs and char['bbox'][0] - runs[-1][-1]['bbox'][2] <= .45 * min(
                        char['bbox'][3] - char['bbox'][1], runs[-1][-1]['bbox'][3] - runs[-1][-1]['bbox'][1]):
                    runs[-1].append(char)
                else:
                    runs.append([char])
            surface = _AMOUNT_SURFACE if pattern is _AMOUNT else _UNIT_SURFACE
            for run in runs:
                text = ''.join(unicodedata.normalize('NFKC', c['c']) for c in run)
                if len(text) != len(run):
                    continue
                for match in surface.finditer(text):
                    glyphs = run[match.start():match.end()]
                    selected = any(box[0] - .1 <= (c['bbox'][0] + c['bbox'][2]) / 2 <= box[2] + .1
                                   and box[1] - .1 <= (c['bbox'][1] + c['bbox'][3]) / 2 <= box[3] + .1 for c in glyphs)
                    if selected:
                        contained = all(box[0] - .1 <= c['bbox'][0] and box[1] - .1 <= c['bbox'][1]
                                        and c['bbox'][2] <= box[2] + .1 and c['bbox'][3] <= box[3] + .1 for c in glyphs)
                        observed.append((_norm(match.group()), contained))
    if (len(observed) != 1 or not observed[0][1] or observed[0][0] != _norm(anchor['quote'])
            or not pattern.fullmatch(observed[0][0])):
        raise ValueError('原格金额或单位不是原页完整词元')
    return observed[0][0]

def fact_source_errors(fact, expected, pages, pdf_sha, validate_source):
    errors = []
    cell = fact.get('source_cell')
    if not isinstance(cell, dict) or cell.get('schema') != FACT_SOURCE_VERSION:
        return ['事实缺少v2独立原格/行/列/单位绑定']
    if not expected or expected.get('source_cell_digest') != digest(cell):
        errors.append('事实原格与独立操作数契约不符')
    anchors = {k: cell.get(k) for k in ('cell', 'row', 'column', 'table', 'unit_source')}
    for name, source in anchors.items():
        if not isinstance(source, dict) or not source.get('bbox'):
            errors.append(f'事实缺少{name}原文定位')
        else:
            errors.extend(validate_source(source, pages, pdf_sha))
    if errors:
        return errors
    page = anchors['cell'].get('page')
    if any(s.get('page') != page for s in anchors.values()):
        return ['事实行/列/格/表/单位未绑定同一物理页']
    box = anchors['cell']['bbox']
    table, row, col = (anchors[k]['bbox'] for k in ('table', 'row', 'column'))
    if not all(table[0] <= s['bbox'][0] < s['bbox'][2] <= table[2]
               and table[1] <= s['bbox'][1] < s['bbox'][3] <= table[3] for s in anchors.values()):
        errors.append('原格或身份定位位于指定表外')
    if not (max(row[1], box[1]) < min(row[3], box[3])
            and max(col[0], box[0]) < min(col[2], box[2]) and col[3] <= box[1] + 1):
        errors.append('原格不在独立行标/列头的交点')
    def norm(value):
        return re.sub(r'\s+', '', str(value))
    # 顶层引文必须指向同一个金额格；同页另一个真实数字不构成本值证明。
    if (fact.get('page', fact.get('physical_page')) != page
            or norm(fact.get('quote')) != norm(anchors['cell']['quote'])
            or (fact.get('bbox') is not None and fact['bbox'] != box)):
        errors.append('事实引文与独立原格不一致')
    if anchors['unit_source']['quote'] != cell.get('raw_unit') or cell.get('raw_unit') not in FACTORS:
        errors.append('原文单位未绑定')
    try:
        original = whole_source_token(pages[page - 1], anchors['cell'], _AMOUNT)
        unit = whole_source_token(pages[page - 1], anchors['unit_source'], _UNIT)
        if not _AMOUNT.fullmatch(_norm(pages[page - 1].get_textbox(box))):
            raise ValueError('原格含未支持的金额表示')
        raw = Decimal(_norm(cell['raw_value']).replace(',', ''))
        value = Decimal(str(fact['value_wan']))
        if (not raw.is_finite() or not value.is_finite()
                or Decimal(original.replace(',', '')) != raw
                or unit != cell['raw_unit'] or value != raw * FACTORS[unit]):
            errors.append('原格数值或单位换算与事实值不符')
    except (KeyError, TypeError, ValueError, InvalidOperation):
        errors.append('原格数值或完整单位非法')
    return errors
