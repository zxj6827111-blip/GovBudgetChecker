"""只读原 PDF，先冻结独立参照；此脚本不导入产品解析器或 pdfplumber。"""
import hashlib
import json
import math
from pathlib import Path
import re

import pymupdf
from PIL import Image, ImageDraw

BASE = Path(__file__).resolve().parent


def clustered(values, tolerance=1.1):
    groups = []
    for value in sorted(values):
        if groups and value - groups[-1][-1] <= tolerance:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [sum(g) / len(g) for g in groups]


def vector_cells(page):
    horizontal, vertical = [], []

    def line(x0, y0, x1, y1):
        if abs(y1-y0) < .6 and abs(x1-x0) > 2:
            horizontal.append(((y0+y1)/2, min(x0, x1), max(x0, x1)))
        elif abs(x1-x0) < .6 and abs(y1-y0) > 2:
            vertical.append(((x0+x1)/2, min(y0, y1), max(y0, y1)))

    for drawing in page.get_drawings():
        for item in drawing['items']:
            if item[0] == 'l':
                line(item[1].x, item[1].y, item[2].x, item[2].y)
            elif item[0] == 're':
                r = item[1]
                if r.height < 2:
                    line(r.x0, (r.y0+r.y1)/2, r.x1, (r.y0+r.y1)/2)
                elif r.width < 2:
                    line((r.x0+r.x1)/2, r.y0, (r.x0+r.x1)/2, r.y1)
                elif drawing['type'] != 'f':
                    line(r.x0, r.y0, r.x1, r.y0)
                    line(r.x0, r.y1, r.x1, r.y1)
                    line(r.x0, r.y0, r.x0, r.y1)
                    line(r.x1, r.y0, r.x1, r.y1)
    if not horizontal or not vertical:
        return []
    xs = clustered([x for x, _, _ in vertical])
    ys = clustered([y for y, _, _ in horizontal])
    if len(xs) < 2 or len(ys) < 2:
        return []

    def covers(lines, axis, lo, hi):
        intervals = sorted((a, b) for pos, a, b in lines if abs(pos-axis) <= 1.2)
        reach = lo
        for a, b in intervals:
            if b < reach - 1.2:
                continue
            if a > reach + 1.2:
                break
            reach = max(reach, b)
            if reach >= hi - 1.2:
                return True
        return False

    nr, nc = len(ys)-1, len(xs)-1
    parent = list(range(nr*nc))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        parent[find(a)] = find(b)

    for r in range(nr):
        for c in range(nc):
            k = r*nc+c
            if c+1 < nc and not covers(vertical, xs[c+1], ys[r], ys[r+1]):
                union(k, k+1)
            if r+1 < nr and not covers(horizontal, ys[r+1], xs[c], xs[c+1]):
                union(k, k+nc)
    groups = {}
    for r in range(nr):
        for c in range(nc):
            groups.setdefault(find(r*nc+c), []).append((r, c))
    result = []
    for units in groups.values():
        r0, r1 = min(r for r, c in units), max(r for r, c in units)+1
        c0, c1 = min(c for r, c in units), max(c for r, c in units)+1
        if len(units) != (r1-r0)*(c1-c0):
            continue
        x0, y0, x1, y1 = xs[c0], ys[r0], xs[c1], ys[r1]
        if x1-x0 < 3 or y1-y0 < 3:
            continue
        if not (covers(horizontal,y0,x0,x1) and covers(horizontal,y1,x0,x1)
                and covers(vertical,x0,y0,y1) and covers(vertical,x1,y0,y1)):
            continue
        result.append([round(v,3) for v in (x0,y0,x1,y1)])
    return sorted(result,key=lambda b:(b[1],b[0]))


def page_chars(page):
    return [dict(text=c['c'], bbox=list(c['bbox']), origin=list(c['origin']))
            for block in page.get_text('rawdict')['blocks'] if block['type'] == 0
            for ln in block['lines'] for span in ln['spans'] for c in span['chars']
            if c['c'].strip()]


def text_in(chars, box):
    x0,y0,x1,y1 = box
    chosen = [c for c in chars if x0-.1 <= (c['bbox'][0]+c['bbox'][2])/2 <= x1+.1
              and y0-.1 <= (c['bbox'][1]+c['bbox'][3])/2 <= y1+.1]
    lines = []
    for c in sorted(chosen,key=lambda c:(c['origin'][1],c['origin'][0])):
        if lines and abs(c['origin'][1]-lines[-1][0]['origin'][1]) < 2:
            lines[-1].append(c)
        else:
            lines.append([c])
    return '\n'.join(''.join(c['text'] for c in sorted(ln,key=lambda c:c['origin'][0])) for ln in lines)


if __name__ == '__main__':
    selection = json.loads((BASE/'selection.json').read_text())
    freeze = dict(method='PyMuPDF original vectors + independent elementary-grid union, original characters, no product imports, original rendered images',documents=[])
    for item in selection['documents']:
        doc = pymupdf.open(item['source_pdf'])
        record = dict(item,pages=[])
        thumbs, table_thumbs = [], []
        for n,page in enumerate(doc,1):
            chars=page_chars(page)
            boxes=vector_cells(page)
            cells=[dict(bbox=b,text=text_in(chars,b)) for b in boxes]
            page_record=dict(page=n,width=page.rect.width,height=page.rect.height,
                             text=page.get_text(),cells=cells,chars=chars,
                             images=len(page.get_images()),table_candidate=len(cells)>=4)
            record['pages'].append(page_record)
            pix=page.get_pixmap(matrix=pymupdf.Matrix(1.6,1.6),alpha=False)
            img=Image.frombytes('RGB',[pix.width,pix.height],pix.samples)
            if page_record['table_candidate']:
                img.save(BASE/'images'/f'{item["id"]}_p{n:02}.png')
            thumb=img.copy();thumb.thumbnail((360,510))
            frame=Image.new('RGB',(380,540),'#dddddd');frame.paste(thumb,(10,25))
            ImageDraw.Draw(frame).text((10,5),f'{item["id"]} PDF page {n}',fill='black')
            thumbs.append(frame)
            if page_record['table_candidate']:
                table_thumbs.append((n,img))
        overview=Image.new('RGB',(380*4,540*math.ceil(len(thumbs)/4)),'white')
        for k,img in enumerate(thumbs):overview.paste(img,((k%4)*380,(k//4)*540))
        overview.save(BASE/'images'/f'{item["id"]}_all_pages.png')
        for k in range(0,len(table_thumbs),6):
            group=table_thumbs[k:k+6]
            contact=Image.new('RGB',(600*3,880*math.ceil(len(group)/3)),'#eeeeee')
            for j,(n,img) in enumerate(group):
                im=img.copy();im.thumbnail((590,840))
                x,y=(j%3)*600,(j//3)*880
                contact.paste(im,(x,y+25));ImageDraw.Draw(contact).text((x+10,y+5),f'{item["id"]} PDF page {n}',fill='black')
            contact.save(BASE/'images'/f'{item["id"]}_tables_{k//6+1}.png')
        (BASE/'source'/f'{item["id"]}.json').write_text(json.dumps(record,ensure_ascii=False))
        freeze['documents'].append(dict(id=item['id'],source_sha256=item['sha256'],source_truth_sha256=hashlib.sha256((BASE/'source'/f'{item["id"]}.json').read_bytes()).hexdigest(),pages=len(doc),table_candidate_pages=[p['page'] for p in record['pages'] if p['table_candidate']],cell_count=sum(len(p['cells']) for p in record['pages'])))
        print(json.dumps(freeze['documents'][-1]),flush=True)
    (BASE/'source_frozen_manifest.json').write_text(json.dumps(freeze,ensure_ascii=False,indent=2))
