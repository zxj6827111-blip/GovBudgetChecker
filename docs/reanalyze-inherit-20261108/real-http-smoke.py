import json
import urllib.request
import urllib.error
from pathlib import Path

BASE = "http://127.0.0.1:18744"
KEY = "gbc-verify-key-2026"
UPLOADS = Path("/Volumes/workSSD/software development/gbc-worktrees/reanalyze-inherit-20261108/uploads-verify")
RESULTS = []


def call(method, path, token=None, body=None):
    headers = {"X-API-Key": KEY, "Content-Type": "application/json"}
    if token:
        headers["X-Session-Token"] = token
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


def status_of(job):
    return json.loads((UPLOADS / job / "status.json").read_text())


def put_models(token, *, enabled, default_use_ai, active, profiles):
    _, current = call("GET", "/api/admin/ai-models", token)
    revision = current.get("revision", current.get("settings", {}).get("revision", 0))
    body = {"revision": revision, "enabled": enabled, "default_use_ai": default_use_ai,
            "active_profile_id": active, "profiles": profiles}
    return call("PUT", "/api/admin/ai-models", token, body)


def profile(pid, model):
    return {"id": pid, "name": f"验证{pid}", "provider_type": "openai_compat",
            "base_url": "https://example.com/v1", "model": model, "api_key": "sk-verify"}


# --- 登录 + 强制改密 ---
_, login = call("POST", "/api/auth/login", body={"username": "admin", "password": "VerifyAdmin#2026"})
token = login["token"]
call("POST", "/api/auth/change-password", token, {"old_password": "VerifyAdmin#2026", "new_password": "VerifyAdmin#2026b"})
_, login = call("POST", "/api/auth/login", body={"username": "admin", "password": "VerifyAdmin#2026b"})
token = login["token"]
check("登录并改密成功", bool(token))

# --- 配置：系统默认 AI 开，当前模型 model-b（与上轮 model-a 不同）---
code, _ = put_models(token, enabled=True, default_use_ai=True, active="model-b",
                     profiles=[profile("model-a", "model-old"), profile("model-b", "model-b")])
check("写入模型配置（系统默认AI开/当前model-b）", code == 200, f"HTTP {code}")

# --- 场景1：系统默认开，但上轮仅本地 → 继承不得打开 AI ---
code, preview = call("GET", "/api/jobs/job-verify-local/reanalyze-options", token)
check("预览本地任务可用", code == 200 and preview["inherit"]["available"] is True)
check("预览展示上轮仅本地", preview["inherit"]["use_ai_assist"] is False)
code, resp = call("POST", "/api/jobs/job-verify-local/reanalyze", token, {})
after = status_of("job-verify-local")
check("系统默认开不影响继承（AI仍关）", code == 200 and after["use_ai_assist"] is False and after["mode"] == "legacy",
      f"HTTP {code} ai={after.get('use_ai_assist')} mode={after.get('mode')}")
check("响应标记 inherit", resp.get("check_method") == "inherit")

# --- 场景2：原模型配置变化 → 提示但可执行，继承原 model-a 而非当前 model-b ---
code, _ = put_models(token, enabled=True, default_use_ai=True, active="model-b",
                     profiles=[profile("model-a", "model-new"), profile("model-b", "model-b")])
code, preview = call("GET", "/api/jobs/job-verify-ai/reanalyze-options", token)
check("预览检测到模型配置变化", code == 200 and preview["model"]["state"] == "changed",
      f"state={preview.get('model', {}).get('state')}")
code, resp = call("POST", "/api/jobs/job-verify-ai/reanalyze", token, {})
after = status_of("job-verify-ai")
check("模型变化仍按原模型执行并告警", code == 200 and after["ai_profile_id"] == "model-a"
      and any("配置已变化" in w for w in resp.get("inheritance_warnings", [])),
      f"HTTP {code} profile={after.get('ai_profile_id')} warnings={resp.get('inheritance_warnings')}")

# --- 场景3：原模型删除 → 422 明确提示且不清除已有结果 ---
# 用一个新种子任务保持 done 终态
import shutil
d = UPLOADS / "job-verify-deleted"
d.mkdir(exist_ok=True)
shutil.copyfile(UPLOADS / "seed-src.pdf", d / "source.pdf")
(d / "status.json").write_text(json.dumps({"job_id": "job-verify-deleted", "status": "done", "mode": "dual",
    "use_local_rules": True, "use_ai_assist": True, "ai_profile_id": "model-a",
    "ai_configuration": {"id": "model-a", "name": "验证model-a", "model": "model-old", "revision": 1},
    "filename": "source.pdf", "result": {"summary": "preserved"}}, ensure_ascii=False))
(d / "fact_context.json").write_text('{"preserved":true}')
before_status = (d / "status.json").read_bytes()
before_fact = (d / "fact_context.json").read_bytes()
code, _ = put_models(token, enabled=True, default_use_ai=True, active="model-b", profiles=[profile("model-b", "model-b")])
code, preview = call("GET", "/api/jobs/job-verify-deleted/reanalyze-options", token)
check("预览检测到原模型已删除", code == 200 and preview["model"]["state"] == "deleted")
code, resp = call("POST", "/api/jobs/job-verify-deleted/reanalyze", token, {})
check("模型删除拒绝并明确提示", code == 422 and "已删除" in str(resp.get("detail", "")), f"HTTP {code} {resp.get('detail')}")
check("模型删除不清除已有结果", (d / "status.json").read_bytes() == before_status and (d / "fact_context.json").read_bytes() == before_fact)

# --- 场景4：历史配置缺失 → 标未知，409 拒绝猜测 ---
code, preview = call("GET", "/api/jobs/job-verify-unknown/reanalyze-options", token)
check("预览标记历史未知", code == 200 and preview["inherit"]["available"] is False
      and set(preview["inherit"]["unknown_fields"]) == {"mode", "use_local_rules", "use_ai_assist"})
before = (UPLOADS / "job-verify-unknown" / "status.json").read_bytes()
code, resp = call("POST", "/api/jobs/job-verify-unknown/reanalyze", token, {})
check("历史缺失拒绝继承", code == 409 and "没有已记录" in str(resp.get("detail", "")), f"HTTP {code}")
check("历史缺失不清除结果", (UPLOADS / "job-verify-unknown" / "status.json").read_bytes() == before)

# --- 场景5：显式选择大模型 + 本次模型 ---
# 无 worker 消费，任务停在 queued；先把该任务复位到终态再验证显式选择。
local_status = status_of("job-verify-local")
local_status["status"] = "done"
(UPLOADS / "job-verify-local" / "status.json").write_text(json.dumps(local_status, ensure_ascii=False))
code, resp = call("POST", "/api/jobs/job-verify-local/reanalyze", token, {"check_method": "ai", "ai_profile_id": "model-b"})
after = status_of("job-verify-local")
check("显式AI选择生效", code == 200 and resp.get("check_method") == "ai" and after["use_ai_assist"] is True
      and after["ai_profile_id"] == "model-b" and after["mode"] == "dual", f"HTTP {code} {after.get('ai_profile_id')}")

# --- 场景6：系统停用大模型 → 继承 AI 开启的任务被拒且不清结果 ---
d = UPLOADS / "job-verify-disabled"
d.mkdir(exist_ok=True)
shutil.copyfile(UPLOADS / "seed-src.pdf", d / "source.pdf")
(d / "status.json").write_text(json.dumps({"job_id": "job-verify-disabled", "status": "done", "mode": "dual",
    "use_local_rules": True, "use_ai_assist": True, "ai_profile_id": "model-b", "filename": "source.pdf",
    "result": {"summary": "preserved"}}, ensure_ascii=False))
before = (d / "status.json").read_bytes()
code, _ = put_models(token, enabled=False, default_use_ai=False, active=None, profiles=[profile("model-b", "model-b")])
code, resp = call("POST", "/api/jobs/job-verify-disabled/reanalyze", token, {})
check("系统停用拒绝继承AI", code == 422 and "停用" in str(resp.get("detail", "")), f"HTTP {code} {resp.get('detail')}")
check("系统停用不清除结果", (d / "status.json").read_bytes() == before)

print("\n=== 汇总 ===")
passed = sum(1 for _, ok, _ in RESULTS if ok)
print(f"{passed}/{len(RESULTS)} passed")
Path("/tmp/gbc-reverify-http-results.json").write_text(json.dumps(
    [{"name": n, "passed": ok, "detail": dt} for n, ok, dt in RESULTS], ensure_ascii=False, indent=2))
raise SystemExit(0 if passed == len(RESULTS) else 1)