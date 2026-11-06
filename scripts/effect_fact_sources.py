"""独立事实的原格、表头、行标和单位必须形成同一空间证据链。"""
from decimal import Decimal, InvalidOperation
import re

from scripts.acceptance_artifacts import digest

FACT_SOURCE_VERSION = 'independent-fiscal-cell-v2'
FACTORS = {'元': Decimal('.0001'), '万元': Decimal('1'), '亿元': Decimal('10000'), 'wan': Decimal('1'), 'yuan': Decimal('.0001')}


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
        actual = pages[page - 1].get_textbox(box)
        tokens = re.findall(r'(?<![\d.])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\d.])', actual)
        raw = Decimal(str(cell['raw_value']).replace(',', ''))
        value = Decimal(str(fact['value_wan']))
        if (not raw.is_finite() or not value.is_finite() or len(tokens) != 1
                or Decimal(tokens[0].replace(',', '')) != raw
                or value != raw * FACTORS[cell['raw_unit']]):
            errors.append('原格数值或单位换算与事实值不符')
    except (KeyError, TypeError, InvalidOperation):
        errors.append('原格数值或单位非法')
    return errors
