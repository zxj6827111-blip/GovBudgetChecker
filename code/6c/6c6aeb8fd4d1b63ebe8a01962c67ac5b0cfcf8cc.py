"""Independent source-only PDF review aids. Never imports project parsers."""
from pathlib import Path
import hashlib
import json
import pdfplumber

SOURCE = Path('/Users/zhangxingjin/Desktop/决算测试上海市')
OUT = Path(__file__).parent / 'raw'
for label, name in [('信访', '上海市信访办公室'), ('教育', '上海市教育委员会'), ('生态', '上海市生态环境局')]:
    path = SOURCE / (name + '.pdf')
    pages = []
    with pdfplumber.open(path) as doc:
        for index, page in enumerate(doc.pages, 1):
            pages.append({'page': index, 'width': page.width, 'height': page.height,
                          'text': page.extract_text(layout=False) or '',
                          'words': page.extract_words()})
    (OUT / (label + '.txt')).write_text('\n\n'.join('=== PDF PAGE ' + str(p['page']) + ' ===\n' + p['text'] for p in pages), encoding='utf-8')
    (OUT / (label + '_source.json')).write_text(json.dumps({'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'pages': pages}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(label, 'pages=', len(pages), 'sha256=', hashlib.sha256(path.read_bytes()).hexdigest())
