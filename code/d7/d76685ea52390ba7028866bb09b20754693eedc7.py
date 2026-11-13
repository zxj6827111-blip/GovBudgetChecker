#!/usr/bin/env python3
"""阶段D v3（R2-06）：隔离数据库完整链路，绑定候选代码身份。

覆盖：
- 首次上传成功（200，非 409 复用）；
- 状态/复核上下文/开复核/合法写回/re-read；
- 权限负控（受限用户访问他人材料被拒）；
- 重分析后旧会话失效；**新会话已存在时携带旧 analysis_basis_token 写回被 409 拒**；
- 新会话携带当前 basis 写回正控；
- 旧 revision 完整读取与历史导出；
- JSON/CSV/PDF/UI 同 revision、同 review_report_sha256、同问题值；
- expected_revision=999 只作 decision 乐观锁负控，不冒充分析版本负控。
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import time
from pathlib import Path

import asyncpg
import httpx

BASE = "http://127.0.0.1:18848"
FRONTEND = "http://127.0.0.1:3103"
API_KEY = "gbcround3testkey0123456789abc"
DB_URL = "postgresql://gbc_test:gbc_test_pw@127.0.0.1:55433/gbc_closeout2"
SAMPLE = Path("/Users/zhangxingjin/Desktop/预决算信息收集/决算测试上海市/2023/"
              "上海市绿化和市容管理局/上海市环境学校2023年度决算.pdf")
ORG_FILE = Path("/Volumes/workSSD/software development/GovBudgetChecker/data/organizations.json")


def dump(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=1, default=str) + "\n"
    path.write_text(text, encoding="utf-8")


def check(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"ASSERT FAILED: {message}")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--skip-ui", action="store_true")
    args = parser.parse_args()
    out = args.out
    out.mkdir(parents=True, exist_ok=False)
    steps = []

    def step(name: str, **payload) -> None:
        steps.append({"step": name, **payload})

    check(SAMPLE.exists(), f"样本不存在：{SAMPLE}")
    async with httpx.AsyncClient(base_url=BASE, timeout=180.0) as client:
        login = await client.post("/api/auth/login", headers={"X-API-Key": API_KEY},
                                  json={"username": "admin", "password": "admin"})
        check(login.status_code == 200, f"admin 登录失败：{login.text[:300]}")
        admin_token = login.json()["token"]
        admin = {"X-API-Key": API_KEY, "X-Session-Token": admin_token}
        step("login_admin", status=login.status_code)

        # 1) 首次上传：必须是新建，不接受 409 复用
        upload = await client.post(
            "/api/documents/upload", headers=admin,
            files={"file": (SAMPLE.name, SAMPLE.read_bytes(), "application/pdf")},
            data={"enqueue": "true", "mode": "legacy", "use_local_rules": "true",
                  "use_ai_assist": "false", "fiscal_year": "2023", "doc_type": "dept_final"},
        )
        check(upload.status_code == 200, f"首次上传不是 200：{upload.status_code} {upload.text[:300]}")
        uploaded = upload.json()
        dump(out / "01_upload.json", uploaded)
        job_id = str(uploaded.get("job_id") or (uploaded.get("job") or {}).get("job_id"))
        check(bool(job_id), "上传未返回 job_id")
        check(uploaded.get("material_registration", {}).get("status") == "registered",
              "材料未登记到数据库")
        step("upload_first", status=upload.status_code, job_id=job_id,
             slot_id=(uploaded.get("material_registration") or {}).get("material_slot", {}).get("slot_id"),
             document_version_id=(uploaded.get("material_registration") or {}).get("document_version_id"))

        # 2) 状态
        deadline = time.time() + 600
        status = {}
        while time.time() < deadline:
            response = await client.get(f"/api/jobs/{job_id}/status", headers=admin)
            check(response.status_code == 200, f"status 读取失败：{response.text[:200]}")
            status = response.json()
            if status.get("status") in {"review_required", "completed", "failed", "error"}:
                break
            await asyncio.sleep(3)
        dump(out / "02_status.json", status)
        check(status.get("status") == "review_required", f"分析未进入复核：{status.get('status')}")
        old_revision = str(status.get("analysis_revision") or "")
        check(bool(old_revision), "状态缺 analysis_revision")
        step("status", state=status.get("status"), analysis_revision=old_revision)

        # 3) 复核上下文与开复核
        review = await client.get("/api/reviews", headers=admin, params={"job_uuid": job_id})
        check(review.status_code == 200, f"review 读取失败：{review.text[:200]}")
        data = review.json()["data"]
        check((data.get("review_context") or {}).get("available") is True, "复核上下文不可用")
        slot_id = str((data.get("review_context") or {}).get("slot_id") or "")
        check(bool(slot_id), "缺 slot_id")
        dump(out / "03_review.json", data)
        start = await client.post(f"/api/reviews/{slot_id}/start", headers=admin,
                                  json={"job_uuid": job_id})
        check(start.status_code == 200, f"开复核失败：{start.status_code} {start.text[:300]}")
        step("start_session_1", status=start.status_code, slot_id=slot_id)

        detail = await client.get(f"/api/reviews/{slot_id}", headers=admin,
                                  params={"job_uuid": job_id})
        check(detail.status_code == 200, f"复核详情失败：{detail.text[:200]}")
        state = detail.json()["data"]
        dump(out / "04_review_detail_old.json", state)
        basis_old = str((state.get("current_analysis") or {}).get("analysis_basis_token") or "")
        check(bool(basis_old), "缺旧 analysis_basis_token")
        items = (state.get("obligation_review") or {}).get("items") or []
        blocking = [item for item in items if item.get("status") not in {"completed", "not_applicable"}]
        check(bool(blocking), "没有可写回的阻塞义务")
        obligation_id = str(blocking[0]["obligation_id"])
        step("obligation_1", obligation_id=obligation_id, basis_token=basis_old,
             pending=len(blocking))

        # 4) 合法写回 + re-read
        decision = await client.put(
            f"/api/reviews/{slot_id}/obligations/{obligation_id}", headers=admin,
            json={"decision": "verified_ok", "note": "R-06 首次写回", "job_uuid": job_id},
        )
        check(decision.status_code == 200, f"首次写回失败：{decision.status_code} {decision.text[:300]}")
        dump(out / "05_decision_first.json", decision.json())
        again = await client.get(f"/api/reviews/{slot_id}", headers=admin,
                                 params={"job_uuid": job_id})
        state2 = again.json()["data"]
        row = next((item for item in (state2.get("obligation_review") or {}).get("items") or []
                    if item.get("obligation_id") == obligation_id), {})
        check(row.get("decision") == "verified_ok", "写回后 re-read 未看到 verified_ok")
        check(int(row.get("decision_revision") or 0) >= 1, "写回 revision 未增长")
        dump(out / "06_review_after_first.json", state2)

        # 5) 权限负控：受限用户不能读该材料
        orgs = json.loads(ORG_FILE.read_text(encoding="utf-8"))
        org_list = orgs if isinstance(orgs, list) else (orgs.get("organizations") or [])
        by_id = {str(org.get("id")): org for org in org_list if isinstance(org, dict)}
        job_org = str(uploaded.get("organization_id") or "")
        # 受限用户必须不在 job 组织的祖先链上；授权祖先会合法覆盖后代。
        ancestors, cursor = set(), job_org
        while cursor and cursor in by_id:
            ancestors.add(cursor)
            cursor = str(by_id[cursor].get("parent_id") or "")
        foreign = next((str(org.get("id")) for org in org_list
                        if isinstance(org, dict) and org.get("id")
                        and str(org.get("id")) not in ancestors
                        and str(org.get("id")) != job_org), "")
        check(bool(foreign), "找不到可用于权限负控的其他组织（非祖先）")
        viewer_name = "r06_viewer"
        created = await client.post("/api/users", headers=admin, json={
            "username": viewer_name, "password": "R06-viewer-pass", "is_admin": False,
            "organization_ids": [foreign],
        })
        if created.status_code == 400 and "exist" in created.text.lower():
            # 用户存储是文件级的，跨轮次会保留；显式改回本轮的非祖先组织。
            patched = await client.patch(f"/api/users/{viewer_name}", headers=admin, json={
                "organization_ids": [foreign], "is_admin": False, "is_active": True,
                "is_read_only": False,
            })
            check(patched.status_code == 200, f"受限用户改组织失败：{patched.text[:200]}")
        else:
            check(created.status_code == 200, f"受限用户创建失败：{created.status_code} {created.text[:200]}")
        viewer_login = await client.post("/api/auth/login", headers={"X-API-Key": API_KEY},
                                         json={"username": viewer_name, "password": "R06-viewer-pass"})
        check(viewer_login.status_code == 200, f"受限用户登录失败：{viewer_login.text[:200]}")
        viewer = {"X-API-Key": API_KEY, "X-Session-Token": viewer_login.json()["token"]}
        denied_review = await client.get(f"/api/reviews/{slot_id}", headers=viewer)
        denied_job = await client.get(f"/api/jobs/{job_id}/status", headers=viewer)
        check(denied_review.status_code in {403, 404}, f"受限用户读复核未被拒：{denied_review.status_code}")
        check(denied_job.status_code in {403, 404}, f"受限用户读任务未被拒：{denied_job.status_code}")
        dump(out / "07_permission_negative.json", {
            "review": denied_review.status_code, "job": denied_job.status_code,
            "foreign_org": foreign, "job_org": job_org})
        step("permission_negative", review=denied_review.status_code, job=denied_job.status_code)

        # 6) 重分析：旧会话/旧 basis 写回必须被拒
        reanalyze = await client.post(f"/api/jobs/{job_id}/reanalyze", headers=admin, json={})
        check(reanalyze.status_code == 200, f"重分析失败：{reanalyze.status_code} {reanalyze.text[:300]}")
        new_revision = ""
        deadline = time.time() + 600
        while time.time() < deadline:
            response = await client.get(f"/api/jobs/{job_id}/status", headers=admin)
            payload = response.json()
            if payload.get("analysis_revision") and payload["analysis_revision"] != old_revision \
                    and payload.get("status") in {"review_required", "completed"}:
                new_revision = str(payload["analysis_revision"])
                break
            await asyncio.sleep(3)
        check(bool(new_revision), "重分析未产生新 revision")
        stale_write = await client.put(
            f"/api/reviews/{slot_id}/obligations/{obligation_id}", headers=admin,
            json={"decision": "verified_issue", "note": "旧会话写回探针", "job_uuid": job_id},
        )
        check(stale_write.status_code in {409, 403}, f"旧会话写回未被拒：{stale_write.status_code} {stale_write.text[:300]}")
        dump(out / "08_stale_session_rejected.json", {"status": stale_write.status_code,
                                                      "body": stale_write.text[:800]})
        step("stale_session_rejected", status=stale_write.status_code,
             old_basis=basis_old, new_revision=new_revision)

        # 7) 新会话合法写回正控
        start2 = await client.post(f"/api/reviews/{slot_id}/start", headers=admin,
                                   json={"job_uuid": job_id})
        check(start2.status_code == 200, f"新会话开启失败：{start2.status_code} {start2.text[:300]}")
        detail2 = await client.get(f"/api/reviews/{slot_id}", headers=admin,
                                   params={"job_uuid": job_id})
        state3 = detail2.json()["data"]
        basis_new = str((state3.get("current_analysis") or {}).get("analysis_basis_token") or "")
        check(basis_new and basis_new != basis_old, "新会话 basis 未变化")
        items2 = (state3.get("obligation_review") or {}).get("items") or []
        blocking2 = [item for item in items2 if item.get("status") not in {"completed", "not_applicable"}]
        check(bool(blocking2), "新会话没有可写回义务")
        obligation2 = str(blocking2[0]["obligation_id"])

        # 6b) 新会话已存在时，携带旧 analysis_basis_token 必须被拒（R2-06）
        stale_basis_write = await client.put(
            f"/api/reviews/{slot_id}/obligations/{obligation2}", headers=admin,
            json={"decision": "verified_issue", "note": "旧分析依据写回探针",
                  "job_uuid": job_id, "analysis_basis_token": basis_old},
        )
        check(stale_basis_write.status_code == 409,
              f"旧 analysis_basis_token 未被拒：{stale_basis_write.status_code} {stale_basis_write.text[:300]}")
        try:
            stale_body = stale_basis_write.json()
        except Exception:  # noqa: BLE001
            stale_body = {}
        stale_error = str((stale_body.get("detail") or {}).get("error")
                          if isinstance(stale_body.get("detail"), dict) else "")
        check(stale_error == "analysis_basis_stale",
              f"旧 basis 拒绝码不是 analysis_basis_stale：{stale_body}")
        dump(out / "09b_stale_basis_rejected.json", {
            "status": stale_basis_write.status_code, "body": stale_body,
            "old_basis": basis_old, "new_basis": basis_new,
        })
        step("stale_basis_rejected", status=stale_basis_write.status_code,
             error=stale_error, old_basis=basis_old, new_basis=basis_new)

        decision2 = await client.put(
            f"/api/reviews/{slot_id}/obligations/{obligation2}", headers=admin,
            json={"decision": "verified_ok", "note": "R-06 新会话写回", "job_uuid": job_id,
                  "analysis_basis_token": basis_new},
        )
        check(decision2.status_code == 200, f"新会话写回失败：{decision2.status_code} {decision2.text[:300]}")
        dump(out / "09_new_session_decision.json", decision2.json())
        step("new_session_write", status=decision2.status_code, basis_new=basis_new,
             obligation_id=obligation2)

        # 8) 旧 revision 完整读取与关键值对照
        old_read = await client.get(f"/api/jobs/{job_id}", headers=admin,
                                    params={"analysis_revision": old_revision})
        check(old_read.status_code == 200, f"旧 revision 读取失败：{old_read.status_code}")
        old_payload = old_read.json()
        dump(out / "10_old_revision_full.json", old_payload)
        check(str(old_payload.get("analysis_revision") or "") == old_revision,
              "旧 revision 返回的 revision 不一致")
        current_read = await client.get(f"/api/jobs/{job_id}", headers=admin)
        check(current_read.status_code == 200, "当前 revision 读取失败")
        current_payload = current_read.json()
        dump(out / "11_current_revision_full.json", current_payload)

        # 9) 导出（当前 revision）并做 JSON/CSV/PDF 同值核对
        review_report = current_payload.get("review_report") or {}
        formal_issues = [item for item in review_report.get("issues") or []
                         if isinstance(item, dict) and item.get("id")]
        check(bool(formal_issues), "当前 revision 没有可核对的问题")
        target_issue = formal_issues[0]
        target_id = str(target_issue.get("id") or "")
        target_message = str(target_issue.get("message") or "")
        target_snippet = re.sub(r"\s+", "", target_message)[:60]
        check(bool(target_snippet), "目标问题没有可核对文本")

        hashes = {}
        json_sha = ""
        json_revision = ""
        for fmt in ("json", "csv", "pdf"):
            resp = await client.get("/api/reports/download", headers=admin,
                                    params={"job_id": job_id, "format": fmt})
            check(resp.status_code == 200, f"{fmt} 导出失败：{resp.status_code}")
            body = resp.content
            check(len(body) > 1000, f"{fmt} 导出过小：{len(body)}")
            path = out / f"12_export.{fmt}"
            path.write_bytes(body)
            hashes[fmt] = {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
            if fmt == "json":
                exported = json.loads(body.decode("utf-8"))
                dump(out / "13_export_json_parsed.json", exported)
                json_revision = str(exported.get("analysis_revision") or "")
                json_sha = str(exported.get("review_report_sha256") or "")
                check(json_revision == new_revision,
                      f"导出 JSON revision {json_revision} ≠ 当前 {new_revision}")
                exported_target = next((item for item in exported.get("issues") or []
                                        if str(item.get("id") or "") == target_id), None)
                check(exported_target is not None, f"JSON 导出缺目标问题 {target_id}")
                check(target_snippet in re.sub(r"\s+", "", str(exported_target.get("message") or "")),
                      "JSON 导出目标问题文本不一致")
            elif fmt == "csv":
                import csv
                import io
                rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
                summary_rows = [row for row in rows if row.get("record_type") == "summary"]
                check(bool(summary_rows), "CSV 缺 summary 行")
                check(str(summary_rows[0].get("analysis_revision") or "") == new_revision,
                      "CSV summary revision 与当前不一致")
                check(str(summary_rows[0].get("review_report_sha256") or "") == json_sha,
                      "CSV summary 的 review_report_sha256 与 JSON 不一致")
                csv_target = next((row for row in rows
                                   if str(row.get("issue_id") or "") == target_id), None)
                check(csv_target is not None, f"CSV 缺目标问题 {target_id}")
                csv_text = re.sub(r"\s+", "", str(csv_target.get("message")
                                                 or csv_target.get("原始消息") or ""))
                check(target_snippet in csv_text, "CSV 目标问题文本不一致")
            else:
                check(body[:5] == b"%PDF-", "导出 PDF 头不合法")
                import pymupdf
                with pymupdf.open(stream=body, filetype="pdf") as doc:
                    pdf_text = "\n".join(page.get_text() for page in doc)
                compact = re.sub(r"\s+", "", pdf_text)
                check(json_sha and f"REVIEW_REPORT_SHA256:{json_sha}" in compact,
                      "PDF 缺与 JSON 相同的 REVIEW_REPORT_SHA256")
                check(f"ISSUE_ID|{target_id}|" in compact, f"PDF 缺目标问题 {target_id}")
                check(target_snippet[:40] in compact, "PDF 目标问题文本不一致")
        dump(out / "14_export_hashes.json", {
            **hashes, "analysis_revision": json_revision,
            "review_report_sha256": json_sha,
            "target_issue": {"id": target_id, "message": target_message[:300]},
        })
        step("exports", hashes=hashes, analysis_revision=json_revision,
             review_report_sha256=json_sha, target_issue=target_id)

        # 9b) 旧 revision 导出仍绑定旧 revision（版本化导出，不串到当前）
        old_export = await client.get("/api/reports/download", headers=admin,
                                      params={"job_id": job_id, "format": "json",
                                              "analysis_revision": old_revision})
        check(old_export.status_code == 200, f"旧 revision 导出失败：{old_export.status_code}")
        old_export_payload = old_export.json()
        dump(out / "14b_old_revision_export.json", old_export_payload)
        check(str(old_export_payload.get("analysis_revision") or "") == old_revision,
              "旧 revision 导出返回了其他 revision")
        check(old_export_payload.get("review_report_sha256") != json_sha,
              "旧 revision 导出与当前 revision 指纹相同")
        step("old_revision_export", analysis_revision=old_revision,
             review_report_sha256=old_export_payload.get("review_report_sha256"))

        # 10) DB 断言
        conn = await asyncpg.connect(DB_URL)
        try:
            revisions = await conn.fetch(
                "select revision_id from analysis_revision_results order by created_at")
            revision_ids = [str(row["revision_id"]) for row in revisions]
            check(old_revision in revision_ids and new_revision in revision_ids,
                  f"数据库 revision 缺失：{revision_ids}")
            decisions = await conn.fetch(
                "select obligation_id, decision, revision, reviewer from review_obligation_decisions "
                "where slot_id=$1 order by revision", slot_id)
            check(len(decisions) >= 2, f"数据库写回少于 2 条：{len(decisions)}")
            check(any(str(row["decision"]) == "verified_ok" for row in decisions),
                  "数据库没有 verified_ok 写回")
            sessions = await conn.fetch(
                "select id, analysis_basis_token, status from review_sessions where slot_id=$1 "
                "order by started_at", slot_id)
            bases = [str(row["analysis_basis_token"]) for row in sessions]
            check(basis_old in bases, "数据库缺旧会话 basis")
            dump(out / "15_db_assertions.json", {
                "revision_ids": revision_ids,
                "decisions": [dict(row) for row in decisions],
                "sessions": [dict(row) for row in sessions],
            })
            step("db_assertions", revisions=revision_ids, decisions=len(decisions),
                 sessions=len(sessions))
        finally:
            await conn.close()

        # 11) UI 页面（新会话后）：Node Playwright 子进程，失败即非 0
        if not args.skip_ui:
            import subprocess
            ui_script = Path(__file__).resolve().parent / "ui_probe_r3.js"
            ui = subprocess.run(
                ["node", str(ui_script), job_id, str(out), FRONTEND, target_snippet],
                capture_output=True, text=True,
                env={**__import__("os").environ, "NODE_PATH":
                     "/Volumes/workSSD/software development/GovBudgetChecker/app/node_modules"},
            )
            (out / "15_ui_stdout.log").write_text(ui.stdout + "\n" + ui.stderr, encoding="utf-8")
            check(ui.returncode == 0, f"UI 页面探针失败：{ui.stderr[:300]}")
            ui_payload = json.loads((out / "15_ui_probe.json").read_text(encoding="utf-8"))
            check(ui_payload.get("review_has_queue") and ui_payload.get("review_has_sample"),
                  "复核页面未显示待办/样本")
            check(ui_payload.get("history_has_job"), "历史页面未显示任务")
            check(ui_payload.get("review_has_target_issue"), "复核页面未显示目标问题文本")
            step("ui", pages=["review", "history"], target_issue=target_id)

        # 12) decision 乐观锁负控（单独，不冒充分析版本负控）
        lock = await client.put(
            f"/api/reviews/{slot_id}/obligations/{obligation2}", headers=admin,
            json={"decision": "verified_issue", "note": "乐观锁探针", "job_uuid": job_id,
                  "expected_revision": 999},
        )
        check(lock.status_code == 409, f"错误 decision revision 未被拒：{lock.status_code}")
        dump(out / "20_decision_lock.json", {"status": lock.status_code, "body": lock.text[:500]})
        step("decision_lock", status=lock.status_code)

    dump(out / "00_steps.json", steps)
    print(json.dumps({"ok": True, "steps": len(steps), "job_id": job_id,
                      "old_revision": old_revision, "new_revision": new_revision},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
