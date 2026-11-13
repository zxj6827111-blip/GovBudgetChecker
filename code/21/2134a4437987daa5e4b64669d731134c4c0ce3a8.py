"""只读下载已有检查表指出的官方原件，记录哈希；不产生业务身份标签。"""
from pathlib import Path
from datetime import datetime, timezone
import asyncio
import hashlib
import json
import httpx

OUT = Path(__file__).resolve().parent / 'official_budget_sources'
OUT.mkdir(exist_ok=False)
SOURCES = {
    'construction_department_2025': 'https://www.shpt.gov.cn/zhengwu/jgwbb-jgw/2025/51/194634/9bfc689f51654567b9106ec37734b619.pdf',
    'transport_center_unit_2025': 'https://www.shpt.gov.cn/zhengwu/shsptqjtglzx-jgw/2025/51/194633/1d91e3e883cc447782f1394d61fa63bb.pdf',
    'data_bureau_unit_2025': 'https://www.shpt.gov.cn/zhengwu/shujj-zfbm/2025/77/195306/f44ee474f4be4094a7a7fb6ec8436a49.pdf',
    'discipline_unit_2025': 'https://www.shpt.gov.cn/zhengwu/jw-dwbm/2025/70/195148/dd4aae5287c54643a2d3213ceff11d0a.pdf',
    'discipline_department_2025': 'https://www.shpt.gov.cn/zhengwu/jw-dwbm/2025/70/195146/93a3442bdb944c77aacb8c9f332a96d3.pdf',
}


async def main():
    async with httpx.AsyncClient(timeout=40, follow_redirects=True) as client:
        async def fetch(name, url):
            row = {'source_url': url, 'candidate_name': name, 'fetched_at': datetime.now(timezone.utc).isoformat(),
                   'identity_label_granted': False}
            try:
                response = await client.get(url)
                row.update(status=response.status_code, resolved_url=str(response.url), content_type=response.headers.get('content-type'))
                response.raise_for_status()
                blob = response.content
                assert blob.startswith(b'%PDF') and 0 < len(blob) <= 20 * 1024 * 1024, 'not a bounded PDF'
                target = OUT / (name + '.pdf')
                target.write_bytes(blob)
                row.update(path=str(target), sha256=hashlib.sha256(blob).hexdigest(), bytes=len(blob), downloaded=True)
            except Exception as exc:
                row.update(downloaded=False, error=str(exc)[:500])
            return row
        results = await asyncio.gather(*(fetch(name, url) for name, url in SOURCES.items()))
    (OUT / 'download-manifest.json').write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'downloaded': sum(row['downloaded'] for row in results), 'total': len(results),
                      'manifest': str(OUT / 'download-manifest.json')}, ensure_ascii=False))


asyncio.run(main())
