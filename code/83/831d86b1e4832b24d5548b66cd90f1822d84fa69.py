#!/usr/bin/env python3
"""阶段D：隔离数据库 + 真实 HTTP/API/导出/复核写回链路验证。

前置：
- 隔离 Postgres 容器（127.0.0.1:55432，库 gbc_closeout）
- 隔离后端 127.0.0.1:18846，DATABASE_URL 指向上述库，UPLOAD/ORG/TMP 隔离
脚本只调用隔离实例，不触碰 5432 生产库与既有服务。
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:18846"
API_KEY = "gbccloseouttestkey0123456789abcd"
SAMPLE_PDF = Path(
    "/Users/zhangxingjin/Desktop/预决算信息收集/决算测试上海市/2023/"
    "上海市绿化和市容管理局/上海市环境学校2023年度决算.pdf"
)
DB_URL = "postgresql://gbc_test:gbc_test_pw@127.0.0.1:55432/gbc_closeout"


def write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, (dict, list)):
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str) + "\n",
                        encoding="utf-8")
    else:
        path.write_text(str(payload), encoding="utf-8")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out
    out.mkdir(parents=True, exist_ok=False)
    log = []

    def record(step: str, payload) -> None:
        log.append({"step": step, **payload})

    if not SAMPLE_PDF.exists():
        sample = None
        for candidate in Path("/Users/zhangxingjin/Desktop/预决算信息收集/决算测试上海市").rglob("*.pdf"):
            sample = candidate
            break
        if sample is None:
            raise SystemExit("找不到可用样本 PDF")
    else:
        sample = SAMPLE_PDF

    async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as client:
        login = await client.post("/api/auth/login", headers={"X-API-Key": API_KEY},
                                  json={"username": "admin", "password": "admin"})
        login.raise_for_status()
        token = login.json()["token"]
        headers = {"X-API-Key": API_KEY, "X-Session-Token": token}
        record("login", {"status": login.status_code, "user": login.json()["user"]["username"]})

        # 1) 真实上传（入队）
        content = sample.read_bytes()
        upload = await client.post(
            "/api/documents/upload", headers=headers,
            files={"file": (sample.name, content, "application/pdf")},
            data={"enqueue": "true", "mode": "legacy", "use_local_rules": "true",
                  "use_ai_assist": "false", "fiscal_year": "2023", "doc_type": "dept_final"},
        )
        uploaded = {}
        if upload.status_code == 409:
            # 隔离库已登记同一 checksum：复用既有任务，不重复建版本。
            import re as _re
            match = _re.search(r"任务\s*([0-9a-f]{32})", upload.text)
            if not match:
                raise SystemExit(f"重复上传但无法解析既有任务：{upload.text[:300]}")
            job_id = match.group(1)
            write(out / "01_upload_duplicate.json",
                  {"status": upload.status_code, "detail": upload.text, "reused_job_id": job_id})
        else:
            upload.raise_for_status()
            uploaded = upload.json()
            write(out / "01_upload.json", uploaded)
            job_id = (uploaded.get("job") or {}).get("job_id") or uploaded.get("job_id")
            if not job_id:
                raise SystemExit(f"上传未返回 job_id：{uploaded}")
        record("upload", {"status": upload.status_code, "job_id": job_id})

        # 2) 轮询状态
        status = {}
        deadline = time.time() + 600
        while time.time() < deadline:
            resp = await client.get(f"/api/jobs/{job_id}/status", headers=headers)
            resp.raise_for_status()
            status = resp.json()
            state = str(status.get("status") or "")
            if state in {"review_required", "completed", "failed", "error"}:
                break
            await asyncio.sleep(3)
        write(out / "02_status.json", status)
        record("status", {"state": status.get("status"),
                          "analysis_revision": status.get("analysis_revision")})
        if str(status.get("status")) not in {"review_required", "completed"}:
            raise SystemExit(f"分析未成功：{status.get('status')} {status.get('error')}")

        # 3) 复核上下文
        review = await client.get("/api/reviews", headers=headers, params={"job_uuid": job_id})
        review.raise_for_status()
        review_data = review.json()["data"]
        write(out / "03_review.json", review_data)
        slot_id = (review_data.get("review_context") or {}).get("slot_id") or \
                  (review_data.get("review") or {}).get("slot_id")
        record("review", {"slot_id": slot_id,
                          "issues": len((review_data.get("review") or {}).get("problems") or []),
                          "obligations": len((review_data.get("review") or {}).get("coverage_items") or [])})

        # 4) 开复核
        start = await client.post(f"/api/reviews/{slot_id}/start", headers=headers,
                                  json={"job_uuid": job_id})
        start_body = start.json() if start.content else {}
        write(out / "04_start.json", {"status": start.status_code, "body": start_body})
        record("start", {"status": start.status_code,
                         "already_active": start.status_code == 409})

        # 5) 选一条阻塞义务，合法人工确认写回
        detail = await client.get(f"/api/reviews/{slot_id}", headers=headers,
                                  params={"job_uuid": job_id})
        detail.raise_for_status()
        state = detail.json()["data"]
        write(out / "05_review_detail.json", state)
        obligations = (state.get("obligation_review") or {}).get("items") or []
        blocking = [item for item in obligations
                    if item.get("status") not in {"completed", "not_applicable"}]
        if not blocking:
            raise SystemExit("没有可写回的阻塞义务")
        target = blocking[0]
        decision = await client.put(
            f"/api/reviews/{slot_id}/obligations/{target['obligation_id']}", headers=headers,
            json={"decision": "verified_ok", "note": "隔离环境真实写回验证",
                  "job_uuid": job_id},
        )
        if decision.status_code == 409:
            # 上一次验证已写过同一义务：按当前 revision 合法更新一次。
            current = state.get("obligation_review", {}).get("items") or []
            row = next((entry for entry in current
                        if entry.get("obligation_id") == target["obligation_id"]), {})
            revision = int(row.get("decision_revision") or 1)
            decision = await client.put(
                f"/api/reviews/{slot_id}/obligations/{target['obligation_id']}", headers=headers,
                json={"decision": "verified_ok", "note": "隔离环境真实写回验证（更新）",
                      "job_uuid": job_id, "expected_revision": revision},
            )
        write(out / "06_decision.json", {"status": decision.status_code, "body": decision.json()})
        record("decision", {"status": decision.status_code,
                            "obligation_id": target["obligation_id"]})

        # 6) 重新读取，确认写回生效
        again = await client.get(f"/api/reviews/{slot_id}", headers=headers,
                                 params={"job_uuid": job_id})
        again.raise_for_status()
        state2 = again.json()["data"]
        write(out / "07_review_after.json", state2)
        decisions = state2.get("obligation_decisions") or {}
        row = decisions.get(target["obligation_id"]) if isinstance(decisions, dict) else None
        record("reread", {"decision_row": row,
                          "gate": state2.get("completion_gate")})

        # 7) 错误 revision 提交应被拒绝（不覆盖新结果）
        stale = await client.put(
            f"/api/reviews/{slot_id}/obligations/{target['obligation_id']}", headers=headers,
            json={"decision": "verified_issue", "note": "stale revision probe",
                  "job_uuid": job_id, "expected_revision": 999},
        )
        write(out / "08_stale_revision.json", {"status": stale.status_code, "body": stale.text[:800]})
        record("stale_revision", {"status": stale.status_code})

        # 8) 导出 JSON/CSV/PDF
        for fmt in ("json", "csv", "pdf"):
            resp = await client.get("/api/reports/download", headers=headers,
                                    params={"job_id": job_id, "format": fmt})
            body = resp.content
            suffix = {"json": "json", "csv": "csv", "pdf": "pdf"}[fmt]
            (out / f"09_export.{suffix}").write_bytes(body)
            record(f"export_{fmt}", {"status": resp.status_code, "bytes": len(body),
                                     "sha256": hashlib.sha256(body).hexdigest()})

        # 9) 数据库读回：版本、写回、审计
        import asyncpg

        conn = await asyncpg.connect(DB_URL)
        try:
            tables = await conn.fetch(
                "select table_name from information_schema.tables "
                "where table_schema='public' order by table_name")
            names = [row["table_name"] for row in tables]
            write(out / "10_db_tables.json", names)
            probes = {}
            for table in ("material_slots", "analysis_revisions", "document_versions",
                          "review_sessions", "review_problem_decisions",
                          "review_obligation_decisions", "audit_events"):
                if table not in names:
                    probes[table] = "MISSING"
                    continue
                rows = await conn.fetch(f'select * from "{table}" limit 200')
                probes[table] = [dict(row) for row in rows[:5]]
            write(out / "11_db_rows.json", probes)
            record("db_read", {"tables": len(names)})
        finally:
            await conn.close()

    write(out / "00_steps.json", log)
    print(json.dumps(log, ensure_ascii=False, indent=1)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
