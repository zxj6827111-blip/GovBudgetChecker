import json,urllib.request,hashlib
from pathlib import Path
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[1];MAIN=ROOT/'artifacts/daily-main';build=MAIN/'app/.next';expected=json.loads((OUT/'release_candidate.json').read_text());op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
assert (build/'BUILD_ID').read_text().strip()==expected['build_id']
manifest=json.loads((build/'app-build-manifest.json').read_text());routes=[key for key in manifest['pages'] if key.endswith('/review/page')];assert len(routes)==1;files=manifest['pages'][routes[0]];rows=[]
for name in files:
 path='/_next/'+name
 with op.open('http://127.0.0.1:3000'+path,timeout=20) as response:data=response.read();assert response.status==200
 local=build/name;assert data==local.read_bytes();rows.append(dict(path=path,sha256=hashlib.sha256(data).hexdigest()))
http={}
for port,path in [(18743,'/health'),(18743,'/ready'),(3000,'/login')]:
 with op.open(f'http://127.0.0.1:{port}{path}',timeout=20) as response:http[f'{port}{path}']=response.status;assert response.status==200
receipt=dict(build_id=expected['build_id'],review_assets_match=True,assets=rows,http=http)
(OUT/'served_build_verification.json').write_text(json.dumps(receipt,indent=2));print('Verified served review bundle',expected['build_id'],len(rows),'assets, health/ready/login200')
