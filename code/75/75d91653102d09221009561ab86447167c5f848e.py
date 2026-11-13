"""两组逐对象直接核对PDF锚点。只记录观察，绝不构造可消费的批准/事实包。"""
from pathlib import Path
import collections, hashlib, json, re
import pymupdf as fitz
ROOT=Path(__file__).resolve().parents[1];R3=ROOT.parents[1]/'outputs/unified_review_remediation_r3_20261106'
def dump(n,v):(ROOT/n).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def norm(x):return re.sub(r'\s+','',str(x or ''))
index=json.loads((ROOT/'pdf_index.json').read_text());docs={r['sample_id']:fitz.open(r['pdf']) for r in index}
source_index={r['sample_id']:r for r in index}
def walk(v):
    if isinstance(v,dict):
        yield v
        for x in v.values():yield from walk(x)
    elif isinstance(v,list):
        for x in v:yield from walk(x)
for fn in ['original9_objects.json','r2_current35_objects.json']:
    objects=json.loads((R3/fn).read_text())['objects'];results=[]
    for i,o in enumerate(objects):
        sid=o.get('origin_group',o.get('sample_id'));doc=docs[sid];anchors={};pages=set()
        for v in walk([o.get('old'),o.get('new')]):
            page=v.get('physical_page',v.get('page',v.get('page_number')))
            if isinstance(page,int) and 1<=page<=len(doc):pages.add(page)
            else:continue
            box=v.get('bbox');quote=v.get('quote',v.get('text_snippet'))
            if box and isinstance(box,list) and len(box)==4 and quote:
                token=(page,tuple(box),norm(quote))
                if token not in anchors:
                    text=doc[page-1].get_textbox(box)
                    anchors[token]={'page':page,'bbox':box,'claimed_quote':quote,'pdf_box_text':text,'quote_matches':norm(quote) in norm(text),'kind':'quote'}
            elif box and v.get('native_blank_verified'):
                token=(page,tuple(box),'blank')
                if token not in anchors:
                    text=doc[page-1].get_textbox(box)
                    anchors[token]={'page':page,'bbox':box,'pdf_box_text':text,'no_printed_number':not bool(re.search(r'\d',text)),'kind':'blank'}
        results.append({'index':i,'sample_id':sid,'key':o['key'],'title':(o.get('new') or o.get('old') or {}).get('title'),'source':source_index[sid], 'observed_physical_pages':sorted(pages),'anchor_count':len(anchors),'anchor_failures':[v for v in anchors.values() if v.get('quote_matches') is False or v.get('no_printed_number') is False],'anchors':list(anchors.values()),'authorization':'NOT_CREATED','semantic_status':'PENDING_EXACT_OBJECT_REVIEW; literal matches do not prove row/column semantics or aggregate completeness'})
    dump(fn.replace('.json','_original_anchor_audit.json'),results)
    print(fn,len(results),'anchors',sum(r['anchor_count'] for r in results),'failures',sum(len(r['anchor_failures']) for r in results),'without_anchors',sum(not r['anchor_count'] for r in results))
# 独立表格提取索引，保留原页全部行列，供人工阅读；不转换为验收事实池。
tables={};selection=[]
for sid,doc in docs.items():
    tables[sid]=[]
    for n,p in enumerate(doc,1):
        t=norm(p.get_text())
        if n>4 and any(label in t for label in ['收入决算表','支出决算表','财政拨款收入支出决算总表','财政拨款“三公”经费支出决算表']):
            found=p.find_tables()
            tables[sid].append({'page':n,'tables':[{'bbox':list(t.bbox),'rows':t.extract()} for t in found.tables]})
            if any(label in t for label in ['收入决算表','财政拨款“三公”经费支出决算表']):selection.append((sid,n))
dump('independent_pdf_tables.json',tables)
from PIL import Image, ImageDraw
(ROOT/'renders').mkdir(exist_ok=True)
# 一页一张保留原尺寸；另建联系表便于审阅全部材料。
thumbs=[]
for sid,n in selection:
    pix=docs[sid][n-1].get_pixmap(matrix=fitz.Matrix(1.4,1.4),alpha=False)
    path=ROOT/'renders'/f'{sid}_p{n}.png';pix.save(path)
    im=Image.open(path).convert('RGB');im.thumbnail((800,650))
    tile=Image.new('RGB',(820,690),'white');tile.paste(im,((820-im.width)//2,30));ImageDraw.Draw(tile).text((10,10),f'{sid} physical page {n}',fill='black');thumbs.append(tile)
for start in range(0,len(thumbs),6):
    batch=thumbs[start:start+6];canvas=Image.new('RGB',(1640,690*((len(batch)+1)//2)),'#ddd')
    for j,t in enumerate(batch):canvas.paste(t,((j%2)*820,(j//2)*690))
    canvas.save(ROOT/'renders'/f'contact_{start//6+1}.png')
for doc in docs.values():doc.close()
