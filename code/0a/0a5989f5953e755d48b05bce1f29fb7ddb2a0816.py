"""按原页标题与表格坐标区分同页多表，不读取被测产物。"""
import json
from pathlib import Path
import re

BASE = Path(__file__).resolve().parent
TITLES = {'01':'收入支出决算总表','02':'收入决算表','03':'支出决算表',
          '04':'财政拨款收入支出决算总表','05':'一般公共预算财政拨款支出决算表',
          '06':'一般公共预算财政拨款基本支出决算表','07':'财政拨款“三公”经费支出决算表',
          '08':'政府性基金预算财政拨款收入支出决算表','09':'国有资本经营预算财政拨款收入支出决算表'}
records = []
for doc in json.loads((BASE / 'inventory.json').read_text()):
    oracle = json.loads((BASE / 'documents' / doc['doc_id'] / 'source_oracle.json').read_text())
    for page in oracle['pages']:
        lines = []
        for word in sorted(page['words'], key=lambda w: ((w[1]+w[3])/2, w[0])):
            y = (word[1]+word[3])/2
            if not lines or abs(y-lines[-1]['y']) > 3:
                lines.append(dict(y=y, words=[]))
            lines[-1]['words'].append(word)
        captions = []
        for line in lines:
            text = re.sub(r'\s+', '', ''.join(w[4] for w in sorted(line['words'],key=lambda w:w[0])))
            matches = [(len(title), code) for code,title in TITLES.items() if title in text]
            if matches and len(text) < max(length for length,_ in matches) + 30:
                _,code = max(matches)
                captions.append(dict(y=line['y'], text=text, code=code, source_words=line['words']))
        for table in page['tables']:
            if 'bbox' not in table:
                continue
            above = [caption for caption in captions if caption['y'] < table['bbox'][1] + 1]
            caption = max(above,key=lambda c:c['y']) if above else None
            code = caption['code'] if caption else page['source_core_code']
            if code:
                records.append(dict(doc_id=doc['doc_id'], page=page['page'], table_index=table['index'],
                                    source_core_code=code, prior_page_code=page['source_core_code'],
                                    source_caption=caption, bbox=table['bbox']))
(BASE / 'source_region_overlay.json').write_text(json.dumps(dict(source_only=True, records=records),ensure_ascii=False,indent=2))
print('source regions',len(records),'page-code refinements',sum(r['source_core_code'] != r['prior_page_code'] for r in records))
