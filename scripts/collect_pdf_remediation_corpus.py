"""下载明确指定的政府公开原件，冻结来源和哈希；不生成解析真值。"""
from __future__ import annotations
import argparse
import hashlib
import html
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit, unquote

import httpx


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--seeds', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / 'manifest.json'
    records = json.loads(path.read_text()) if path.exists() else []
    known = {r['source_url'] for r in records}
    with httpx.Client(timeout=45, follow_redirects=True) as client:
        for seed in json.loads(args.seeds.read_text()):
            source = seed['url']
            host = urlsplit(source).hostname or ''
            if not host.endswith('.gov.cn'):
                raise ValueError('only explicitly supplied government sources are allowed')
            try:
                if urlsplit(source).path.lower().endswith('.pdf'):
                    links = [(source, seed.get('title', unquote(urlsplit(source).path.rsplit('/',1)[-1])))]
                else:
                    response = client.get(source); response.raise_for_status()
                    links = [(urljoin(str(response.url), html.unescape(url)),
                              re.sub('<[^>]+>', '', html.unescape(label)).strip())
                             for url,label in re.findall(r'<a[^>]+href=["\']([^"\']+\.pdf(?:\?[^"\']*)?)["\'][^>]*>(.*?)</a>', response.text, re.I|re.S)]
                for url,label in links[:seed.get('limit', 20)]:
                    if url in known or (seed.get('include') and not re.search(seed['include'],label)):
                        continue
                    if not (urlsplit(url).hostname or '').endswith('.gov.cn'):
                        continue
                    response = client.get(url); response.raise_for_status(); data = response.content
                    if not data.startswith(b'%PDF-') or len(data)>50_000_000:
                        raise ValueError('not an acceptable original PDF')
                    digest = hashlib.sha256(data).hexdigest()
                    file = args.output / (digest + '.pdf')
                    if file.exists() and hashlib.sha256(file.read_bytes()).hexdigest() != digest:
                        raise ValueError('frozen original hash mismatch')
                    if not file.exists(): file.write_bytes(data)
                    record = {'source_url':url,'landing_url':source,'title':label,'sha256':digest,
                              'size_bytes':len(data),'path':str(file.resolve()),'group':seed['group'],
                              'region':seed['region'],'year':seed.get('year'),
                              'document_type':seed['document_type'],'truth_status':'unlabelled'}
                    if any(r['sha256']==digest and r['group']!=record['group'] for r in records):
                        raise ValueError('source leakage between development and independent acceptance')
                    records.append(record);known.add(url)
                    path.write_text(json.dumps(records,ensure_ascii=False,indent=2))
                    print(json.dumps({'title':label,'sha256':digest,'group':seed['group']},ensure_ascii=False),flush=True)
            except (httpx.HTTPError, ValueError) as exc:
                print(json.dumps({'source':source,'error':str(exc)},ensure_ascii=False),flush=True)


if __name__ == '__main__': main()
