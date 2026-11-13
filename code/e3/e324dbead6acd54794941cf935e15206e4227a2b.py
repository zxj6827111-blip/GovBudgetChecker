#!/usr/bin/env python3
"""Batch-1 isolated online validation: disposable PG + API + Worker + review gate + export."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx

ROOT = Path.cwd().resolve()
if not (ROOT / "src").is_dir():
    raise SystemExit(f"run from repository root; got {ROOT}")
sys.path.insert(0, str(ROOT))


def sha(path):
    import hashlib

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def docker(*args, timeout=120):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"docker {args[0]} failed: {result.stderr.strip()}")
    return result.stdout.strip()


async def prepare_db(dsn):
    from src.db.connection import DatabaseConnection
    from src.db.migrations import run_migrations

    # pg_isready 放行时 PostgreSQL 可能仍在完成启动/恢复，连接会被重置；
    # 这里是隔离证据脚本，重试到数据库真正可迁移为止。
    last = None
    for _ in range(60):
        try:
            await DatabaseConnection.initialize(dsn)
            await run_migrations()
            await DatabaseConnection.close()
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            try:
                await DatabaseConnection.close()
            except Exception:
                pass
            await asyncio.sleep(1.0)
    raise last


def request(client, method, url, **kwargs):
    for attempt in range(90):
        response = client.request(method, url, **kwargs)
        if response.status_code == 429:
            time.sleep(max(1.0, float(response.headers.get("Retry-After", "2"))))
            continue
        if response.status_code >= 500 and attempt < 10:
            time.sleep(1.5)
            continue
        return response
    return response


def wait_analysis(client, job_id, timeout=300):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = request(client, "GET", f"/api/jobs/{job_id}/status")
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") not in {"queued", "processing", "running", "pending"}:
            return payload
        time.sleep(1)
    raise TimeoutError("analysis did not finish")


def issue_ids_from_status(status):
    items = status.get("result", {}).get("issues", {}).get("all", [])
    groups = [item for item in items if item.get("rule_id") == "V33-222"]
    symptoms = [item for item in items if item.get("rule_id") == "V33-251"]
    return groups, symptoms, items


def main():
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {'checks': {}, 'errors': []}
    processes, handles = [], []
    name = f'gbc-251-boundaries-{secrets.token_hex(6)}'
    try:
        with tempfile.TemporaryDirectory(prefix='gbc-251-boundaries-') as temporary:
            work = Path(temporary)
            password = secrets.token_hex(24)
            env_file = work / 'postgres.env'
            env_file.write_text(f'POSTGRES_PASSWORD={password}\nPOSTGRES_USER=validator\nPOSTGRES_DB=validation\n')
            env_file.chmod(0o600)
            docker('run', '-d', '--rm', '--pull=never', '--name', name, '--env-file', str(env_file), '-p', '127.0.0.1::5432', 'postgres:16')
            pg_port = int(docker('port', name, '5432/tcp').rsplit(':', 1)[1])
            dsn = f'postgresql://validator:{password}@127.0.0.1:{pg_port}/validation'
            for _ in range(120):
                if subprocess.run(['docker', 'exec', name, 'pg_isready', '-U', 'validator', '-d', 'validation'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                    break
                time.sleep(0.5)
            asyncio.run(prepare_db(dsn))
            env = os.environ.copy()
            for key in list(env):
                if key.startswith(('AI_', 'OPENAI_', 'GEMINI_', 'ZHIPU_', 'DEEPSEEK_', 'CODEX_API_')):
                    env.pop(key)
            password_admin, api_key, session_secret = (secrets.token_hex(24) for _ in range(3))
            env.update(PYTHON_DOTENV_DISABLED='1', TESTING='false', DATABASE_URL=dsn,
                       GOVBUDGET_AUTH_ENABLED='true', GOVBUDGET_API_KEY=api_key,
                       USER_SESSION_SECRET=session_secret, DEFAULT_ADMIN_PASSWORD=password_admin,
                       USER_DATA_DIR=str(work/'users'), ORG_DATA_DIR=str(work/'orgs'),
                       UPLOAD_DIR=str(work/'uploads'), AUDIT_LOG_PATH=str(output/'audit.jsonl'),
                       AI_ASSIST_ENABLED='false', AI_ASSIST_REQUIRED='false', AI_ENABLED='false',
                       OCR_ENABLED='false', JOB_QUEUE_ENABLED='true', JOB_QUEUE_INLINE_FALLBACK='false',
                       JOB_QUEUE_WORKERS='1', JOB_QUEUE_POLL_INTERVAL_SECONDS='0.2',
                       READY_REQUIRE_DATABASE='true', READY_REQUIRE_AI_EXTRACTOR='false')
            port = free_port()
            for role, args in [('api', ['-m', 'uvicorn', 'api.main:app', '--host', '127.0.0.1', '--port', str(port)]), ('worker', ['-m', 'api.worker'])]:
                handle = (output/f'{role}.log').open('w'); handles.append(handle)
                processes.append(subprocess.Popen([sys.executable, *args], cwd=ROOT, env={**env, 'JOB_QUEUE_ROLE': role}, stdout=handle, stderr=subprocess.STDOUT))
            with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=240, headers={'X-API-Key': api_key}) as client:
                for _ in range(180):
                    if processes[0].poll() is not None:
                        raise RuntimeError('API exited before startup')
                    try:
                        if client.get('/health').status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.5)
                response = request(client, 'POST', '/api/auth/login', json={'username':'admin','password':password_admin}); response.raise_for_status()
                client.headers['X-Session-Token'] = response.json()['token']
                changed = secrets.token_hex(24)
                request(client, 'POST', '/api/auth/change-password', json={'old_password':password_admin,'new_password':changed}).raise_for_status()
                response = request(client, 'POST', '/api/auth/login', json={'username':'admin','password':changed}); response.raise_for_status()
                client.headers['X-Session-Token'] = response.json()['token']
                request(client, 'GET', '/ready').raise_for_status()
                import pymupdf
                common = ['（三）一般公共预算财政拨款支出决算具体情况',
                          '一般公共预算财政拨款支出年初预算为100.00万元，支出决算为90.00万元。其中：',
                          '1、教育支出（类）普通教育（款）小学教育（项）。',
                          '年初预算100.00万元，支出决算为90.00万元。',
                          '2、住房保障支出（类）住房改革支出（款）住房公积金（项）。']
                for tag, org, sentence, code, total_budget, total_final in [
                    ('adversative','测试甲局','年初预算依法不予公开但支出决算未提供。','item_disclosure_missing','100.00','90.00'),
                    ('operand_conflict','测试乙局','年初无预算，年初预算为60.00万元，支出决算为0.00万元。','exception_amount_conflict','100.00','90.00'),
                    ('ambiguous_amount','测试丙局','年初预算或支出决算依法不予公开，年初预算为60.00万元，支出决算为50.00万元。','exception_scope_ambiguous','160.00','140.00'),
                    ('negated_budget','测试丁局','年初预算并非无此项，预算金额未提供，支出决算为0.00万元。','exception_scope_ambiguous','100.00','90.00'),
                    ('negated_final','测试戊局','支出决算并非无此项，决算金额未提供，年初预算为0.00万元。','exception_scope_ambiguous','100.00','90.00'),
                    ('duplicate_budget','测试己局','年初预算和预算数依法不予公开支出决算未提供。','exception_scope_ambiguous','100.00','90.00'),
                    ('duplicate_final','测试庚局','支出决算和决算数依法不予公开年初预算未提供。','exception_scope_ambiguous','100.00','90.00'),
                    ('pending_budget','测试辛局','年初预算无此项，待确认，支出决算为0.00万元。','exception_scope_ambiguous','100.00','90.00')]:
                    response=request(client,'POST','/api/organizations',json={'name':org,'level':'department'}); response.raise_for_status(); dept=response.json()['id']
                    path=work/f'{tag}.pdf'; pdf=pymupdf.open(); page=pdf.new_page(width=595,height=842)
                    case_common = [line.replace('年初预算为100.00万元，支出决算为90.00万元。其中：', f'年初预算为{total_budget}万元，支出决算为{total_final}万元。其中：') for line in common]
                    # 合成材料分行写入避免行尾超出 PDF 页面；空白由原始管线处理。
                    if tag == 'ambiguous_amount':
                        case_lines = ['年初预算或支出决算依法不予公开，', '年初预算为60.00万元，支出决算为50.00万元。']
                    else:
                        case_lines = [sentence]
                    for index, line in enumerate([f'{org}2023年度部门决算', *case_common, *case_lines, '六、一般公共预算财政拨款基本支出决算情况说明']):
                        page.insert_text((40,60+22*index),line,fontname='china-s',fontsize=10)
                    pdf.save(path); pdf.close()
                    with path.open('rb') as stream:
                        response=request(client,'POST','/api/documents/upload',files={'file':(path.name,stream,'application/pdf')},data={'org_unit_id':dept,'fiscal_year':'2023','doc_type':'dept_final'})
                    response.raise_for_status(); job=response.json()['job_id']
                    request(client,'POST',f'/api/analyze/{job}',json={'mode':'legacy','use_ai_assist':False,'use_local_rules':True}).raise_for_status()
                    status=wait_analysis(client,job); meta=status.get('result',{}).get('meta',{})
                    statuses=(meta.get('rule_execution_summary') or {}).get('rule_statuses') or {}
                    records=[r for r in meta.get('fact_receipts') or [] if str(r.get('sub_check_id') or '').startswith('V33-251:')]
                    codes={c for r in records for c in r.get('reason_codes',[])}
                    coverage=meta.get('obligation_coverage') or {}
                    obligation=next((x for x in coverage.get('instances') or [] if x.get('obligation_id')=='OBL-TXT-FINAL-DETAIL'),{})
                    issues=(status.get('result',{}).get('issues') or {}).get('all') or []
                    report[tag]={'job_id':job,'job_status':status.get('status'),'rule_status':statuses.get('V33-251'),'codes':sorted(codes),'obligation':obligation,'issues':[i for i in issues if i.get('rule_id')=='V33-251'],'receipts':records}
                    if statuses.get('V33-251')!='insufficient_data' or code not in codes or obligation.get('status')!='insufficient_data' or not obligation.get('blocks_gate') or 'OBL-TXT-FINAL-DETAIL' not in (coverage.get('blocking_obligation_ids') or []):
                        raise AssertionError(f'{tag}: rule or gate incorrect: {report[tag]}')
                    for issue in report[tag]['issues']:
                        loc=issue.get('location') or {}; included={(x.get('kind'),x.get('pos')) for x in loc.get('included_items') or []}; excluded={(x.get('kind'),x.get('pos')) for x in loc.get('not_included_items') or []}
                        if included & excluded or (tag=='operand_conflict' and '已超过' in str(issue.get('message') or '')):
                            raise AssertionError(f'{tag}: invalid operand evidence: {loc}')
                    report['checks'][tag]='pass'
    except Exception as exc:
        import traceback
        report['errors'].append(f'{type(exc).__name__}: {exc}'); report['traceback']=traceback.format_exc()
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=20)
                except subprocess.TimeoutExpired: process.kill()
        for handle in handles: handle.close()
        cleanup=subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60)
        report['container_removed']=cleanup.returncode == 0
        report['passed']=not report['errors'] and report['container_removed']
        (output/'receipt.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps({'passed':report['passed'],'checks':report['checks'],'errors':report['errors'],'container_removed':report['container_removed']},ensure_ascii=False))
    return 0 if report['passed'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
