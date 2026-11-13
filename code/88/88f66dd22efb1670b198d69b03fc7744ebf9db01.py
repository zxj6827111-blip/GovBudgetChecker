"""仅登录隔离实例、只读查询和导出；不提交决定、不重算或发布。"""
from pathlib import Path
import csv
import hashlib
import io
import json
import zipfile
import httpx

OUT = Path(__file__).resolve().parent
CONFIG = Path('/private/tmp/annual-v3/runtime/env.json')
cfg = json.loads(CONFIG.read_text())
assert cfg['PG_SCHEMA'] == 'annual_runtime_verified'
job = '68d4d3ac899f30839ae9c8f7f7b3f29f'
job_dir = Path(cfg['UPLOAD_DIR']) / job
revision = json.loads((job_dir / 'status.json').read_text())['analysis_revision']
summary = {'job_id': job, 'analysis_revision': revision, 'frontend_port': 3308, 'backend_port': 28743}
with httpx.Client(base_url='http://127.0.0.1:3308', timeout=90, follow_redirects=False) as client:
    login = client.post('/api/auth/login', json={
        'username': cfg['DEFAULT_ADMIN_USERNAME'], 'password': cfg['DEFAULT_ADMIN_PASSWORD'],
    })
    assert login.status_code == 200, login.status_code
    summary['normal_login_status'] = login.status_code
    for route in ['/settings', '/api/admin/templates', f'/api/jobs/{job}/material-dimensions']:
        response = client.get(route)
        assert response.status_code == 200, (route, response.status_code)
        info = {'status': response.status_code}
        if route == '/api/admin/templates':
            data = response.json()
            info['counts'] = {key: len(value) for key, value in data.items() if isinstance(value, list)}
        elif route.endswith('material-dimensions'):
            data = response.json()
            (OUT / 'material-dimensions.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
        summary[route] = info
    exports = {}
    for fmt in ['json', 'csv', 'pdf', 'docx']:
        response = client.get('/api/reports/download', params={
            'job_id': job, 'format': fmt, 'analysis_revision': revision,
        })
        assert response.status_code == 200, (fmt, response.status_code)
        content = response.content
        assert len(content) > 100
        info = {'status': response.status_code, 'bytes': len(content),
                'sha256': hashlib.sha256(content).hexdigest(),
                'content_type': response.headers.get('content-type')}
        if fmt == 'json':
            data = response.json()
            assert data['analysis_revision'] == revision
            info['material_dimensions'] = data.get('material_dimensions') or (data.get('review_report') or {}).get('metadata', {}).get('material_dimensions')
            assert isinstance(info['material_dimensions'], dict)
        elif fmt == 'csv':
            rows = list(csv.DictReader(io.StringIO(content.decode('utf-8-sig'))))
            info['row_count'] = len(rows)
            dimensions = [json.loads(row['material_dimensions_json']) for row in rows if row.get('material_dimensions_json')]
            assert dimensions and dimensions[0]['writing']['source'] == 'human'
            assert dimensions[0]['amount'] is None
            info['material_dimensions_recorded'] = True
        elif fmt == 'pdf':
            assert content.startswith(b'%PDF')
        else:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                assert 'word/document.xml' in archive.namelist()
                xml = archive.read('word/document.xml').decode()
                info['has_writing_basis_label'] = '写法基准' in xml
        (OUT / ('synthetic-report.' + fmt)).write_bytes(content)
        exports[fmt] = info
    summary['exports'] = exports
for path in ['/health', '/ready']:
    response = httpx.get('http://127.0.0.1:28743' + path, timeout=30)
    assert response.status_code == 200
    summary['backend' + path] = {'status': response.status_code, 'body': response.json()}
(OUT / 'runtime-readback.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'runtime_readback': 'pass', 'revision': revision, 'formats': list(exports)}))
