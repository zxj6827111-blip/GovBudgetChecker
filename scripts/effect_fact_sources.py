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
_AMOUNT_SURFACE = re.compile(
    r'(?:[+\-−(（\[{$￥¥]\s*)*\d[\d,.]*'
    r'(?:\s*[eE]\s*[+\-−]?\s*\d*)?(?:\s*[)）\]}%％])*')


def _norm(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value))).replace('−', '-')


def _contains(box, glyph):
    b = glyph['bbox']
    return (box[0] - .1 <= b[0] and box[1] - .1 <= b[1]
            and b[2] <= box[2] + .1 and b[3] <= box[3] + .1)


def _overlaps_x(a, b):
    return max(a[0], b[0]) < min(a[2], b[2])


def _row_text(page, box):
    """跨 PDF text line 收集同一物理行；字距和 span/line 切分均不能删语义。"""
    glyphs = []
    line_id = 0
    for block in page.get_text('rawdict')['blocks']:
        for line in block.get('lines', []):
            line_id += 1
            if max(line['bbox'][1], box[1]) >= min(line['bbox'][3], box[3]):
                continue
            if line.get('dir', (1, 0)) != (1, 0):
                raise ValueError('原格文字方向无法确认')
            glyphs.extend((c, line_id) for s in line.get('spans', []) for c in s.get('chars', []))
    glyphs.sort(key=lambda item: (item[0]['bbox'][0], item[0]['bbox'][1]))
    text, positions, previous = [], [], None
    for glyph, identity in glyphs:
        if previous is not None and previous != identity:
            # 保留分段为空白，词法仍可跨过它；空白本身不证明原格边界。
            text.append(' ')
            positions.append(None)
        for char in unicodedata.normalize('NFKC', glyph['c']):
            text.append(char)
            positions.append(None if char.isspace() else glyph)
        previous = identity
    return ''.join(text), positions


def _amount_matches(page, text, positions, anchors):
    row, column = anchors['row'], anchors['column']
    # 用原页表头而非金额之间的空隙证明邻列存在。未覆盖的字形、同列多个
    # 金额、跨列表示或没有表头对应的片段均拒收，不能任意分配到最近一列。
    words = [w for w in page.get_text('words')
             if column['bbox'][1] <= (w[1] + w[3]) / 2 <= column['bbox'][3]]
    own = [w for w in words if _overlaps_x(w, column['bbox'])]
    if _norm(''.join(w[4] for w in sorted(own))) != _norm(column['quote']):
        raise ValueError('无法确认完整列头')
    columns = [column['bbox']] + [w[:4] for w in words if w not in own]
    label = _row_label_indices(page, text, positions, row, columns)
    chars = list(text)
    for i in label:
        chars[i] = ' '
    text = ''.join(chars)
    matches, owners = [], []
    consumed = set()
    for match in _AMOUNT_SURFACE.finditer(text):
        glyphs = [g for g in positions[match.start():match.end()] if g]
        bounds = [min(g['bbox'][0] for g in glyphs), 0, max(g['bbox'][2] for g in glyphs), 0]
        aligned = [i for i, c in enumerate(columns) if _overlaps_x(bounds, c)]
        if len(aligned) != 1 or aligned[0] in owners:
            raise ValueError('金额表示缺少唯一原列边界')
        owners.extend(aligned)
        consumed.update(range(match.start(), match.end()))
        matches.append((match, glyphs, aligned[0] == 0))
    if any(not c.isspace() and i not in consumed for i, c in enumerate(text)):
        raise ValueError('原行有未归属完整金额表示的字符')
    return [(m, g) for m, g, selected in matches if selected]


def _row_label_indices(page, text, positions, row, columns):
    """先证明行标与所有金额列及表示互斥，再允许删除；真实子串不是边界证明。"""
    label = {i for i, g in enumerate(positions) if g and _contains(row['bbox'], g)}
    if not label or _norm(''.join(text[i] for i in sorted(label))) != _norm(row['quote']):
        raise ValueError('无法确认原格行标范围')
    # 当前契约中的行标是金额列左侧的身份字段；不能借任一操作数的行标
    # 吞掉其他金额列的前缀、符号或数值，即使引文与摘要完全一致。
    # 与行标起点投影一致的其他表头可属于身份列；当前声明的金额列始终
    # 保留，不能把它借这个排除规则改认成身份列。
    amount_columns = [c for i, c in enumerate(columns)
                      if i == 0 or not c[0] <= row['bbox'][0] < c[2]]
    if row['bbox'][2] > min(c[0] for c in amount_columns):
        raise ValueError('行标范围侵入原页金额列')
    for match in _AMOUNT_SURFACE.finditer(text):
        indices = {i for i in range(match.start(), match.end()) if positions[i]}
        if not indices & label:
            continue
        glyphs = [positions[i] for i in indices]
        bounds = [min(g['bbox'][0] for g in glyphs), 0, max(g['bbox'][2] for g in glyphs), 0]
        if not any(_overlaps_x(bounds, c) for c in amount_columns):
            continue
        # 行标自身可以含横线。仅当它是完整、独立、位于全部金额列左侧
        # 的原页文字组件时，才有依据将其与后面的金额分开；否则保留歧义。
        complete_label = False
        for block in page.get_text('rawdict')['blocks']:
            for line in block.get('lines', []):
                chars = [c for s in line.get('spans', []) for c in s.get('chars', []) if not c['c'].isspace()]
                if (chars and all(_contains(row['bbox'], c) for c in chars)
                        and _norm(''.join(c['c'] for c in chars)) == _norm(row['quote'])
                        and any(c['c'].isalpha() for c in chars)
                        and line['bbox'][2] <= min(c[0] for c in amount_columns)):
                    complete_label = True
        if not complete_label:
            raise ValueError('行标消除会改变完整金额表示')
    return label


def _unit_field(page, text, positions, anchors):
    """单位必须是完整字段；仅可剔除独立绑定的表题和明确的单位标签。"""
    chars = list(text)
    table = anchors.get('table')
    if table:
        titles = []
        for box in page.search_for(table['quote']):
            indices = [i for i, g in enumerate(positions) if g and _contains(box, g)]
            if indices and _norm(''.join(text[i] for i in indices)) == _norm(table['quote']):
                if _contains(table['bbox'], {'bbox': box}):
                    titles.append(indices)
        if len(titles) > 1:
            raise ValueError('单位行表题不唯一')
        if titles:
            for i in titles[0]:
                chars[i] = ' '
    # 正向承认标签结构，不以符号/数量级黑名单猜测其余片段是否无关。
    field = ''.join(chars)
    label = re.match(r'\s*[;；:]?\s*(?:(?:单位|金额单位|Unit)\s*[:：]\s*)?', field)
    start = label.end()
    glyphs = [g for i, g in enumerate(positions) if i >= start and g and not chars[i].isspace()]
    return _norm(field[start:]), glyphs


def whole_source_token(page, anchor, pattern, anchors):
    """先识别含空白的完整表示并证明原列，再验证引文包含全部语义字形。"""
    box = anchor['bbox']
    text, positions = _row_text(page, box)
    if pattern is _AMOUNT:
        tokens = [(_norm(m.group()), g) for m, g in _amount_matches(page, text, positions, anchors)]
    else:
        tokens = [_unit_field(page, text, positions, anchors)]
    observed = []
    for token, glyphs in tokens:
        selected = any(box[0] - .1 <= (g['bbox'][0] + g['bbox'][2]) / 2 <= box[2] + .1
                       and box[1] - .1 <= (g['bbox'][1] + g['bbox'][3]) / 2 <= box[3] + .1 for g in glyphs)
        if selected:
            observed.append((token, all(_contains(box, g) for g in glyphs)))
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
        original = whole_source_token(pages[page - 1], anchors['cell'], _AMOUNT, anchors)
        unit = whole_source_token(pages[page - 1], anchors['unit_source'], _UNIT, anchors)
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
