"""Automated live UAT and isolated disaster recovery; never independent human acceptance."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import time
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import httpx

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def hashes(directory):
    return {
        str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


async def database_snapshot(dsn):
    conn = await asyncpg.connect(dsn)
    try:
        tables = await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
        )
        result = {}
        for table in tables:
            name = table["tablename"]
            quoted = '"' + name.replace('"', '""') + '"'
            row = await conn.fetchrow(
                f"SELECT count(*) AS n, md5(coalesce(string_agg(to_jsonb(t)::text, chr(10) ORDER BY to_jsonb(t)::text), '')) AS digest FROM {quoted} t"
            )
            result[name] = {"rows": row["n"], "ordered_json_md5": row["digest"]}
        return result
    finally:
        await conn.close()


def wait_health(url, process):
    for _ in range(120):
        if process.poll() is not None:
            raise RuntimeError("isolated application exited before readiness")
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.5)
    raise TimeoutError("isolated application did not become ready")


def prepare_isolated_frontend(work, directory, env):
    """在私有临时目录构建，绝不清空当前开发服务使用的 app/.next。"""
    source=ROOT/'app'
    dependencies=source/'node_modules'
    if not dependencies.is_dir():
        raise RuntimeError('frontend dependencies unavailable; no automatic installation performed')
    frontend=work/'frontend'
    common=shutil.ignore_patterns('node_modules','.next','.git','.env','.env.*','*.tsbuildinfo')
    def ignore(directory,names):
        ignored=common(directory,names)
        if Path(directory)==source:
            ignored.update(set(names)&{'coverage','test-results','playwright-report'})
        return ignored
    shutil.copytree(source,frontend,ignore=ignore)
    (frontend/'node_modules').symlink_to(dependencies.resolve(),target_is_directory=True)
    with (directory/'frontend_build.log').open('w') as build_log:
        build=subprocess.run(['node','node_modules/next/dist/bin/next','build'],cwd=frontend,
            env={**env,'NODE_ENV':'production','NEXT_TELEMETRY_DISABLED':'1'},
            stdout=build_log,stderr=subprocess.STDOUT,timeout=600)
    if build.returncode or not (frontend/'.next/BUILD_ID').is_file():
        raise RuntimeError('isolated frontend build failed; see extended/frontend_build.log')
    return frontend


def run_extended_validation(
    *, client, env, work, out, container, pg_port, password, processes, handles, fact_case=None
):
    directory = out / "extended"
    directory.mkdir(exist_ok=True)
    result = {
        "scope": "automated_UAT_and_disaster_recovery",
        "independent_human_acceptance": False,
        "passed": False,
    }
    # Explicitly named test entities, confined to the disposable catalog.
    parent = None
    for level, org_id in [
        ("city", "uat-city"),
        ("district", "uat-district"),
        ("department", "uat-department"),
        ("unit", "uat-unit"),
    ]:
        response = client.post(
            "/api/organizations",
            json={
                "id": org_id,
                "name": {
                    "city": "自动化验收演练市",
                    "district": "自动化验收演练区",
                    "department": "自动化验收演练局",
                    "unit": "自动化验收演练服务中心",
                }[level],
                "level": level,
                "parent_id": parent,
            },
        )
        response.raise_for_status()
        parent = org_id
    from src.services.expected_material_import import COLUMNS

    csv = (
        ",".join(COLUMNS) + "\nuat-unit,unit,2026,final,unit_self,self,2099-12-31T23:59:59+08:00,\n"
    )
    preview = client.post("/api/materials/expected/preview", json={"csv_text": csv})
    preview.raise_for_status()
    body = preview.json()
    if not body["valid"]:
        raise RuntimeError("live CSV preview did not validate exact test identity")
    submission = {"csv_text": csv, "preview_sha256": body["preview_sha256"]}
    imported = client.post("/api/materials/expected/import", json=submission)
    imported.raise_for_status()
    repeated = client.post("/api/materials/expected/import", json=submission)
    repeated.raise_for_status()
    wrong = client.post(
        "/api/materials/expected/import", json={**submission, "preview_sha256": "0" * 64}
    )
    if imported.json()["replayed"] or not repeated.json()["replayed"] or wrong.status_code != 409:
        raise RuntimeError("live CSV repeat or preview mismatch guard failed")
    result["csv_live"] = {
        "atomic_receipt": imported.json(),
        "repeated": repeated.json(),
        "wrong_preview_status": wrong.status_code,
    }
    # A clearly synthetic incomplete final account must remain blocked.
    # Do not automatically manufacture human verified_ok/not_applicable decisions.
    import fitz

    control = work / "automated-review-control.pdf"
    with fitz.open() as pdf:
        page = pdf.new_page(width=650, height=500)
        for y, text in [
            (40, "自动化验收演练服务中心 2026年度单位决算"),
            (70, "收入决算表"),
            (95, "单位：万元"),
            (260, "仅为自动化流程控制材料，缺失说明等待补充。"),
        ]:
            page.insert_text((40, y), text, fontname="china-s", fontsize=11)
        xs, ys = [40, 200, 350, 550], [115, 155, 195]
        for x in xs:
            page.draw_line((x, 115), (x, 195))
        for y in ys:
            page.draw_line((40, y), (550, y))
        for r, row in enumerate(
            [["科目名称", "本年收入合计", "财政拨款收入"], ["合计", "100.00", "100.00"]]
        ):
            for c, text in enumerate(row):
                page.insert_text((xs[c] + 5, ys[r] + 25), text, fontname="china-s", fontsize=10)
        pdf.save(control)
    with control.open("rb") as file:
        uploaded = client.post(
            "/api/documents/upload",
            files={"file": (control.name, file, "application/pdf")},
            data={"org_id": "uat-unit", "fiscal_year": "2026", "doc_type": "final"},
        )
    uploaded.raise_for_status()
    control_job = uploaded.json()["job_id"]
    client.post(
        f"/api/analyze/{control_job}",
        json={"mode": "legacy", "use_local_rules": True, "use_ai_assist": False},
    ).raise_for_status()
    for _ in range(180):
        status = client.get(f"/api/jobs/{control_job}/status")
        if status.status_code == 429:
            time.sleep(2)
            continue
        status.raise_for_status()
        if status.json().get("status") not in {"pending", "queued", "running", "processing"}:
            break
        time.sleep(1)
    else:
        raise TimeoutError("automated review control did not finish")
    review = client.get("/api/reviews", params={"job_uuid": control_job})
    review.raise_for_status()
    context = review.json()["data"]["review_context"]
    if not context["available"]:
        raise RuntimeError("explicit synthetic material did not bind review context")
    control_slot = context["slot_id"]
    started = client.post(f"/api/reviews/{control_slot}/start", json={"job_uuid": control_job})
    (directory / "review_start_response.json").write_text(
        json.dumps(
            {
                "status": started.status_code,
                "body": started.json(),
                "review": review.json(),
                "analysis": status.json(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    started.raise_for_status()
    blocked = client.post(f"/api/reviews/{control_slot}/complete", json={"job_uuid": control_job})
    if blocked.status_code != 409 or not blocked.json().get("detail", {}).get("blockers"):
        raise RuntimeError("incomplete synthetic material incorrectly completed review")
    review_before = client.get(f"/api/reviews/{control_slot}")
    review_before.raise_for_status()
    result["review_control"] = {
        "synthetic": True,
        "job_id": control_job,
        "slot_id": control_slot,
        "start": started.json(),
        "completion_rejected_status": blocked.status_code,
        "completion_rejected": blocked.json(),
        "before_restart": review_before.json(),
        "human_decisions_manufactured": False,
    }
    # 源码与当前服务隔离构建，不借用当前开发缓存或假设已有生产构建。
    frontend_env={**env,'NODE_ENV':'production','NEXT_TELEMETRY_DISABLED':'1',
                  'BACKEND_URL':str(client.base_url).rstrip('/'),
                  'BACKEND_API_KEY':env['GOVBUDGET_API_KEY'],
                  'GBC_ENABLE_LOCAL_AUTH_FALLBACK':'false'}
    frontend_dir=prepare_isolated_frontend(work,directory,frontend_env)
    result['frontend_build']={'isolated':True,'build_id_sha256':hashlib.sha256(
        (frontend_dir/'.next/BUILD_ID').read_bytes()).hexdigest()}
    # Start the built production frontend, authenticated against this API.
    frontend_port = free_port()
    log = (directory / "frontend.log").open("w")
    handles.append(log)
    frontend = subprocess.Popen(
        [
            "node",
            "node_modules/next/dist/bin/next",
            "start",
            "-H",
            "127.0.0.1",
            "-p",
            str(frontend_port),
        ],
        cwd=frontend_dir,
        env=frontend_env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    processes.append(frontend)
    wait_health(f"http://127.0.0.1:{frontend_port}/api/health", frontend)
    browser_script = ROOT / "scripts/validate_real_browser.cjs"
    browser = subprocess.run(
        ["node", str(browser_script)],
        cwd=ROOT / "app",
        env={
            **env,
            "UAT_ORIGIN": f"http://127.0.0.1:{frontend_port}",
            "UAT_PASSWORD": password,
            "UAT_CSV": csv,
            "UAT_OUTPUT": str(directory),
            "UAT_FACT_CASE": json.dumps(fact_case or {}),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=180,
    )
    (directory / "browser.log").write_text(browser.stdout)
    if browser.returncode:
        raise RuntimeError("production browser live UAT failed; see extended/browser.log")
    result["browser_live"] = json.loads((directory / "browser_receipt.json").read_text())
    # 列映射/历史发布演练可能已产生新的当前版本。备份必须对比备份时的
    # 当前结果，并另验最初历史版本，不能拿旧当前导出和新当前导出比较。
    native_status = json.loads((out / 'native_final' / 'status.json').read_text())
    export_job = native_status.get('job_id') or native_status.get('id')
    current_response = client.get(f'/api/jobs/{export_job}/status')
    current_response.raise_for_status()
    expected_current_revision = current_response.json().get('analysis_revision')
    recovery_exports = []
    for view, revision in [('current', None), ('historical', native_status['analysis_revision'])]:
        target = directory / 'recovery_exports' / view
        target.mkdir(parents=True, exist_ok=True)
        for suffix in ('json', 'csv', 'docx', 'pdf'):
            params = {'job_id': export_job, 'format': suffix}
            if revision:
                params['analysis_revision'] = revision
            response = client.get('/api/reports/download', params=params)
            response.raise_for_status()
            expected = target / f'report.{suffix}'
            expected.write_bytes(response.content)
            if view == 'historical' and response.content != (out / 'native_final' / f'report.{suffix}').read_bytes():
                raise RuntimeError('original historical export changed before recovery')
            recovery_exports.append({'params': params, 'view': view, 'path': expected,
                'sha256': hashlib.sha256(response.content).hexdigest()})
    # Quiesce writers so all three components describe a single snapshot.
    for process in list(processes):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
    from scripts import backup_all

    pg_bin = work / "pg-bin"
    pg_bin.mkdir()
    for tool in ("pg_dump", "psql"):
        executable = pg_bin / tool
        executable.write_text(
            "#!/usr/bin/env python3"
            + '\nimport os,subprocess,sys\na=sys.argv[1:]\nfor flag,value in [("-h","127.0.0.1"),("-p","5432")]:\n if flag in a:a[a.index(flag)+1]=value\nsys.exit(subprocess.call(["docker","exec","-i","-e","PGPASSWORD",'
            + repr(container)
            + ","
            + repr(tool)
            + "]+a,env=os.environ))\n"
        )
        executable.chmod(0o700)
    config = {
        key: os.environ.get(key)
        for key in ("PG_BIN_DIR", "ORG_DATA_DIR", "USER_DATA_DIR", "USER_FILE", "TESTING")
    }
    try:
        os.environ.update(
            PG_BIN_DIR=str(pg_bin),
            ORG_DATA_DIR=env["ORG_DATA_DIR"],
            USER_DATA_DIR=env["USER_DATA_DIR"],
            TESTING="false",
        )
        os.environ.pop("USER_FILE", None)
        before_db = asyncio.run(database_snapshot(env["DATABASE_URL"]))
        before_uploads = hashes(Path(env["UPLOAD_DIR"]))
        audit_sha = hashlib.sha256(Path(env["AUDIT_LOG_PATH"]).read_bytes()).hexdigest()
        # Store sensitive recovery archive only in a private local directory.
        archive = directory / "recovery_archive"
        manifest = backup_all.create_backup(
            archive,
            uploads_dir=Path(env["UPLOAD_DIR"]),
            database_url=env["DATABASE_URL"],
            audit_log_path=Path(env["AUDIT_LOG_PATH"]),
        )
        if not backup_all.verify_backup(archive)["ok"]:
            raise RuntimeError("actual backup verification failed")
        subprocess.run(
            ["docker", "exec", container, "createdb", "-U", "validator", "recovery_drill"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        parsed = urlsplit(env["DATABASE_URL"])
        restored_dsn = urlunsplit(parsed._replace(path="/recovery_drill"))
        restore_root = work / "restored"
        restore_root.mkdir()
        backup_all.restore_database(archive, restored_dsn)
        backup_all.restore_uploads(archive, restore_root / "uploads")
        backup_all.restore_application_state(archive, restore_root / "state")
        backup_all.restore_audit_log(archive, restore_root / "audit.jsonl")
        after_db = asyncio.run(database_snapshot(restored_dsn))
        after_uploads = hashes(restore_root / "uploads")
        state_ok = all(
            (restore_root / "state" / name).is_file()
            and hashlib.sha256((restore_root / "state" / name).read_bytes()).hexdigest()
            == info["sha256"]
            for name, info in manifest["artifacts"]["application_state"]["files"].items()
            if info["present"]
        )
        checks = {
            "database_all_tables_identical": before_db == after_db,
            "uploads_all_files_identical": before_uploads == after_uploads,
            "audit_identical": hashlib.sha256(
                (restore_root / "audit.jsonl").read_bytes()
            ).hexdigest()
            == audit_sha,
            "application_state_identical": state_ok,
        }
        if not all(checks.values()):
            raise RuntimeError("restore content verification failed")
        result["recovery"] = {
            **checks,
            "source_database_snapshot": before_db,
            "restored_database_snapshot": after_db,
            "verified_upload_file_count": len(before_uploads),
            "archive_manifest_sha256": hashlib.sha256(
                (archive / "manifest.json").read_bytes()
            ).hexdigest(),
            "passwords_in_receipt": False,
        }
        restored_port = free_port()
        restored_log = (directory / "restored_api.log").open("w")
        handles.append(restored_log)
        restored_env = {
            **env,
            "DATABASE_URL": restored_dsn,
            "UPLOAD_DIR": str(restore_root / "uploads"),
            "ORG_DATA_DIR": str(restore_root / "state"),
            "USER_DATA_DIR": str(restore_root / "state"),
            "AUDIT_LOG_PATH": str(restore_root / "audit.jsonl"),
            "JOB_QUEUE_ROLE": "api",
        }
        restored_api = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "api.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(restored_port),
            ],
            cwd=ROOT,
            env=restored_env,
            stdout=restored_log,
            stderr=subprocess.STDOUT,
        )
        processes.append(restored_api)
        wait_health(f"http://127.0.0.1:{restored_port}/health", restored_api)
        with httpx.Client(
            base_url=f"http://127.0.0.1:{restored_port}",
            headers={"X-API-Key": env["GOVBUDGET_API_KEY"]},
            timeout=180,
        ) as restored:
            login = restored.post(
                "/api/auth/login", json={"username": "admin", "password": password}
            )
            login.raise_for_status()
            restored.headers["X-Session-Token"] = login.json()["token"]
            organizations = restored.get("/api/organizations")
            organizations.raise_for_status()
            if "uat-unit" not in organizations.text:
                raise RuntimeError("restored organization authority not loaded")
            recovered_review = restored.get(f"/api/reviews/{control_slot}")
            recovered_review.raise_for_status()
            before_session = review_before.json()["data"]["current_session"]
            after_session = recovered_review.json()["data"]["current_session"]
            if (
                before_session != after_session
                or recovered_review.json()["data"]["completion_gate"]["can_complete"]
            ):
                raise RuntimeError("review session or blocking gate changed after recovery")
            result["review_control"]["after_restart"] = recovered_review.json()
            result["review_control"]["persisted_and_still_blocked"] = True
            current_response = restored.get(f'/api/jobs/{export_job}/status')
            current_response.raise_for_status()
            if current_response.json().get('analysis_revision') != expected_current_revision:
                raise RuntimeError('restored current analysis revision changed')
            exported = 0
            for expected in recovery_exports:
                download = restored.get(
                    "/api/reports/download", params=expected['params']
                )
                download.raise_for_status()
                if hashlib.sha256(download.content).hexdigest() != expected['sha256']:
                    raise RuntimeError("restored report export changed")
                exported += 1
            result["recovery"]["restarted_login_organization_and_exports_passed"] = True
            result["recovery"]["restarted_verified_exports"] = exported
            result['recovery']['current_and_historical_revision_preserved'] = True
    finally:
        for key, value in config.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    result["passed"] = True
    (directory / "extended_receipt.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2)
    )
    return result
