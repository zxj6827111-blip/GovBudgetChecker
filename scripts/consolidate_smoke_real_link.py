"""三线整合收尾 · 轻量真实链路抽查（2026-11-12）。

隔离栈（一次性 PostgreSQL + 真实 uvicorn API + 真实 Worker，AI/OCR 关闭、
整份确认写侧开启）里新建一次真实文字版决算任务（宜川路街道 2025 部门决算，
既有回归原件），核对：上传/分析、问题对回原件、重分析继承入口、人工复核
决定、整份确认、导出。不冒充新报告盲测，不调用真实模型。

用法：.venv/bin/python scripts/consolidate_smoke_real_link.py --receipt <out.json>
"""
from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

PDF = Path('/Volumes/workSSD/software development/GovBudgetChecker/uploads/'
           '6000a11c0737d5149975516b44fd8682/'
           '上海市普陀区人民政府宜川路街道办事处2025年度部门决算.pdf')


def docker(*args: str) -> str:
    out = subprocess.run(['docker', *args], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args)} 失败: {out.stderr.strip()}")
    return out.stdout.strip()


def free_port() -> int:
    import socket
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def wait_analysis(client: httpx.Client, job_id: str, timeout: float = 600.0) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        answer = client.get(f'/api/jobs/{job_id}/status')
        if answer.status_code == 200:
            last = answer.json()
            if last.get('status') in {'done', 'review_required', 'failed'}:
                return last
        time.sleep(1.0)
    raise TimeoutError(f'分析未在时限内完成: {last}')


def page_text(pdf: Path, page_no: int) -> str:
    import pdfplumber
    with pdfplumber.open(pdf) as handle:
        return handle.pages[page_no - 1].extract_text() or ''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--receipt', required=True)
    args = parser.parse_args()

    receipt: dict = {'pdf': str(PDF), 'checks': []}
    processes: list[subprocess.Popen] = []
    name = f'govbudget-consolidate-smoke-{secrets.token_hex(4)}'
    ok = True

    def check(label: str, passed: bool, detail: str = '') -> None:
        nonlocal ok
        ok = ok and passed
        receipt['checks'].append({'check': label, 'pass': bool(passed), 'detail': detail})
        print(f"[{'PASS' if passed else 'FAIL'}] {label}" + (f' — {detail}' if detail else ''))

    try:
        import asyncio
        from scripts.run_isolated_service_validation import prepare_db

        with tempfile.TemporaryDirectory(prefix='govbudget-consolidate-') as temporary:
            work = Path(temporary)
            env_file = work / 'postgres.env'
            password = secrets.token_hex(24)
            env_file.write_text(
                f'POSTGRES_PASSWORD={password}\nPOSTGRES_USER=validator\n'
                f'POSTGRES_DB=validation\n')
            env_file.chmod(0o600)
            docker('run', '-d', '--rm', '--name', name, '--env-file', str(env_file),
                   '-p', '127.0.0.1::5432', 'postgres:16')
            pg_port = int(docker('port', name, '5432/tcp').rsplit(':', 1)[1])
            dsn = f'postgresql://validator:{password}@127.0.0.1:{pg_port}/validation'
            for _ in range(90):
                if subprocess.run(['docker', 'exec', name, 'pg_isready', '-U',
                                   'validator', '-d', 'validation'],
                                  stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL).returncode == 0:
                    break
                time.sleep(0.5)
            asyncio.run(prepare_db(dsn))

            import os
            env = os.environ.copy()
            for key in list(env):
                if key.startswith(('AI_', 'OPENAI_', 'GEMINI_', 'ZHIPU_',
                                   'DEEPSEEK_', 'CODEX_API_')):
                    env.pop(key)
            admin_password, api_key, session_secret = (secrets.token_hex(24)
                                                       for _ in range(3))
            env.update(
                PYTHON_DOTENV_DISABLED='1', TESTING='false', DATABASE_URL=dsn,
                GOVBUDGET_AUTH_ENABLED='true', GOVBUDGET_API_KEY=api_key,
                USER_SESSION_SECRET=session_secret,
                DEFAULT_ADMIN_PASSWORD=admin_password,
                USER_DATA_DIR=str(work / 'users'), ORG_DATA_DIR=str(work / 'orgs'),
                UPLOAD_DIR=str(work / 'uploads'),
                AUDIT_LOG_PATH=str(work / 'audit.jsonl'),
                AI_ASSIST_ENABLED='false', AI_ASSIST_REQUIRED='false',
                AI_ENABLED='false', OCR_ENABLED='false',
                JOB_QUEUE_ENABLED='true', JOB_QUEUE_INLINE_FALLBACK='false',
                JOB_QUEUE_WORKERS='1', JOB_QUEUE_POLL_INTERVAL_SECONDS='0.2',
                READY_REQUIRE_DATABASE='true', READY_REQUIRE_AI_EXTRACTOR='false',
                GOVBUDGET_CONFIRM_REPORT_ENABLED='1',
            )
            api_port = free_port()
            api_log = (work / 'api.log').open('w')
            worker_log = (work / 'worker.log').open('w')
            processes.append(subprocess.Popen(
                [sys.executable, '-m', 'uvicorn', 'api.main:app', '--host',
                 '127.0.0.1', '--port', str(api_port)], cwd=ROOT,
                env={**env, 'JOB_QUEUE_ROLE': 'api'},
                stdout=api_log, stderr=subprocess.STDOUT))
            processes.append(subprocess.Popen(
                [sys.executable, '-m', 'api.worker'], cwd=ROOT,
                env={**env, 'JOB_QUEUE_ROLE': 'worker'},
                stdout=worker_log, stderr=subprocess.STDOUT))

            with httpx.Client(base_url=f'http://127.0.0.1:{api_port}', timeout=300,
                              headers={'X-API-Key': api_key},
                              trust_env=False) as client:
                for _ in range(120):
                    if processes[0].poll() is not None:
                        raise RuntimeError('API 提前退出，见 api.log')
                    try:
                        if client.get('/health').status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.5)
                client.get('/ready').raise_for_status()

                # 登录（默认管理员口令），首登强制改密后重登，后续请求携带会话令牌
                login = client.post('/api/auth/login', json={
                    'username': 'admin', 'password': admin_password})
                login.raise_for_status()
                client.headers['X-Session-Token'] = login.json()['token']
                rotated = secrets.token_hex(24)
                client.post('/api/auth/change-password', json={
                    'old_password': admin_password, 'new_password': rotated})
                relogin = client.post('/api/auth/login', json={
                    'username': 'admin', 'password': rotated})
                relogin.raise_for_status()
                client.headers['X-Session-Token'] = relogin.json()['token']

                # S0 组织目录
                org = client.post('/api/organizations', json={
                    'name': '上海市普陀区人民政府宜川路街道办事处',
                    'level': 'department'})
                check('S0 组织目录就绪', org.status_code in (200, 201, 422),
                      f'HTTP {org.status_code}')

                # S1 真实上传 + 分析（legacy 本地规则，无 AI）
                with PDF.open('rb') as file:
                    upload = client.post('/api/documents/upload', files={
                        'file': (PDF.name, file, 'application/pdf')})
                upload.raise_for_status()
                job_id = upload.json()['job_id']
                receipt['job_id'] = job_id
                analyze = client.post(f'/api/analyze/{job_id}', json={
                    'mode': 'legacy', 'use_ai_assist': False,
                    'use_local_rules': True})
                analyze.raise_for_status()
                payload = wait_analysis(client, job_id)
                check('S1 上传+分析完成',
                      payload.get('status') in {'done', 'review_required'},
                      json.dumps({k: payload.get(k) for k in
                                  ('status', 'analysis_revision')},
                                 ensure_ascii=False))
                revision = payload.get('analysis_revision')

                # S2 问题清单非空（result 可能是 JSON 串；规范遍历 findings）
                result = payload.get('result') or {}
                if isinstance(result, str):
                    try:
                        result = json.loads(result)
                    except ValueError:
                        result = {}
                from src.services.evidence_guard import iter_findings
                seen: set = set()
                raw_findings = []
                for finding in iter_findings(result if isinstance(result, dict)
                                             else {}):
                    key = str(finding.get('id') or '') or '|'.join(
                        str(finding.get(k) or '') for k in
                        ('rule_id', 'rule', 'page', 'message'))
                    if key not in seen:
                        seen.add(key)
                        raw_findings.append(finding)
                check('S2 问题清单非空', bool(raw_findings),
                      f'findings={len(raw_findings)}')

                # S3 原件定位抽查：问题证据摘录回原件页面文本
                def finding_page(item: dict):
                    location = item.get('location') if isinstance(
                        item.get('location'), dict) else {}
                    return location.get('page') or item.get('page_number')

                spot = []
                for finding in raw_findings:
                    for evidence in (finding.get('evidence') or []):
                        if not isinstance(evidence, dict):
                            continue
                        quote = str(evidence.get('quote') or evidence.get('text')
                                    or evidence.get('text_snippet') or '').strip()
                        ev_page = evidence.get('page') or evidence.get('physical_page')
                        if quote and ev_page:
                            spot.append((int(ev_page), quote))
                            break
                    if len(spot) >= 3:
                        break
                located = 0
                spot_detail = []
                for ev_page, quote in spot:
                    try:
                        text = page_text(PDF, ev_page)
                        needle = quote[:24]
                        hit = needle in text
                        located += 1 if hit else 0
                        spot_detail.append({'page': ev_page, 'quote': quote[:40],
                                            'hit': hit})
                    except Exception as exc:  # noqa: BLE001
                        spot_detail.append({'page': ev_page, 'error': str(exc)})
                check('S3 引用片段回原件命中', bool(spot) and located >= 1,
                      f'抽查{len(spot)}条命中{located}条 {json.dumps(spot_detail, ensure_ascii=False)}')

                # S4 重分析继承入口（线1 功能）：预检反映上轮 legacy/本地规则配置
                options = client.get(f'/api/jobs/{job_id}/reanalyze-options')
                body = options.json() if options.status_code == 200 else {}
                inherit = body.get('inherit') or {}
                check('S4 reanalyze-options 继承本任务 legacy/本地配置',
                      options.status_code == 200
                      and inherit.get('available') is True
                      and inherit.get('mode') == 'legacy'
                      and inherit.get('use_ai_assist') is False
                      and inherit.get('use_local_rules') is True,
                      json.dumps({'http': options.status_code,
                                  'inherit': {k: inherit.get(k) for k in
                                              ('available', 'mode', 'use_local_rules',
                                               'use_ai_assist', 'message')},
                                  'model_state': (body.get('model') or {}).get('state')},
                                 ensure_ascii=False))

                # S5 整份确认门禁 + 确认 + 幂等
                gate = client.get(f'/api/jobs/{job_id}/report-confirmation')
                gate.raise_for_status()
                key = f'smoke-{secrets.token_hex(4)}'
                first = client.post(f'/api/jobs/{job_id}/confirm-report', json={
                    'expected_analysis_revision': revision,
                    'idempotency_key': key})
                record = (first.json().get('record') or {}
                          if first.status_code == 200 else {})
                repeat = client.post(f'/api/jobs/{job_id}/confirm-report', json={
                    'expected_analysis_revision': revision,
                    'idempotency_key': key})
                check('S5 整份确认成功且幂等',
                      first.status_code == 200 and repeat.status_code == 200
                      and repeat.json().get('idempotent') is True
                      and bool(record.get('confirmed_issue_ids')),
                      f'first={first.status_code} repeat={repeat.status_code} '
                      f'confirmed={len(record.get("confirmed_issue_ids") or [])} '
                      f'excluded={json.dumps(record.get("excluded"), ensure_ascii=False)}')
                confirmed_ids = set(record.get('confirmed_issue_ids') or [])

                # S6 人工决定变化：一条问题改 needs_review 后重确认被排除
                info_id = next((f['id'] for f in raw_findings
                                if str(f.get('severity') or '').lower() == 'info'
                                and f.get('id')), None)
                decision_ok = False
                detail = '无可用的 info 级问题'
                if info_id:
                    ignore = client.post('/api/workflow', json={
                        'action': 'update_issue', 'job_id': job_id,
                        'issue_id': info_id, 'status': 'needs_review',
                        'note': '三线整合 smoke：人工标记需复核'})
                    second_key = f'smoke2-{secrets.token_hex(4)}'
                    second = client.post(
                        f'/api/jobs/{job_id}/confirm-report', json={
                            'expected_analysis_revision': revision,
                            'idempotency_key': second_key})
                    second_record = (second.json().get('record') or {}
                                     if second.status_code == 200 else {})
                    decision_ok = (ignore.status_code == 200
                                   and second.status_code == 200
                                   and info_id not in set(
                                       second_record.get('confirmed_issue_ids') or []))
                    detail = (f'ignore={ignore.status_code} '
                              f'second={second.status_code} '
                              f'排除该条={info_id not in set(second_record.get("confirmed_issue_ids") or [])}')
                check('S6 人工决定反映到签认范围', decision_ok, detail)
                confirmed_ids = set(second_record.get('confirmed_issue_ids') or []) \
                    if info_id else confirmed_ids

                # S7 导出：下载并核对成员一致
                export = client.get('/api/reports/download',
                                    params={'job_id': job_id, 'format': 'csv'})
                text = export.text if export.status_code == 200 else ''
                in_export = sum(1 for i in confirmed_ids if i in text)
                check('S7 导出可用且成员与签认一致',
                      export.status_code == 200 and bool(confirmed_ids)
                      and in_export == len(confirmed_ids),
                      f'HTTP={export.status_code} bytes={len(export.content)} '
                      f'成员命中={in_export}/{len(confirmed_ids)}')
                receipt['export_sample'] = text[:400]
            receipt['ok'] = ok
    except Exception as exc:  # noqa: BLE001
        import traceback
        receipt['error'] = f'{type(exc).__name__}: {exc}'
        receipt['traceback'] = traceback.format_exc()[-2000:]
        receipt['ok'] = False
        print(f'异常中断: {exc}')
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=20)
            except Exception:  # noqa: BLE001
                process.kill()
        try:
            docker('rm', '-f', name)
        except Exception:  # noqa: BLE001
            pass
        leftover = subprocess.run(['docker', 'ps', '-aq', '-f', f'name={name}'],
                                  capture_output=True, text=True).stdout.strip()
        receipt['container_cleaned'] = leftover == ''

    Path(args.receipt).write_text(json.dumps(receipt, ensure_ascii=False, indent=1),
                                  encoding='utf-8')
    print(f"\n总体: {'PASS' if ok else 'FAIL'} — 回执 {args.receipt}")
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
