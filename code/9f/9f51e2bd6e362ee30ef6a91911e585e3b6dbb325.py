"""只替换 3000 的前端构建（AI 检查范围单行状态条）；保留旧构建、API、配置和任务数据。"""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import urllib.request

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
manifest = json.loads((OUT / "frontend-source-manifest.json").read_text())
drift = [name for name, digest in manifest.items()
         if not (ROOT / name).is_file() or hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest]
assert not drift, f"源码在构建后被改动：{drift[:10]}"
new = OUT / "build-workspace/app/.next"
assert (new / "BUILD_ID").is_file()
previous = OUT / "previous_app_next"
assert not previous.exists()

api_pid = subprocess.check_output(["lsof", "-tiTCP:18743", "-sTCP:LISTEN"], text=True).strip()
pids = subprocess.check_output(["lsof", "-tiTCP:3000", "-sTCP:LISTEN"], text=True).split()
assert len(set(pids)) == 1
pid = pids[0]
command = subprocess.check_output(["ps", "-p", pid, "-o", "command="], text=True)
cwd = subprocess.check_output(["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"], text=True)
assert "next-server" in command and f"n{ROOT}/app\n" in cwd
old_id = (ROOT / "app/.next/BUILD_ID").read_text().strip()

env = os.environ.copy()
env.update({key: value for key, value in dotenv_values(ROOT / ".env").items() if value is not None})
env.update(BACKEND_URL="http://127.0.0.1:18743", GOVBUDGET_BACKEND_PORT="18743")


def start():
    with (OUT / "frontend.log").open("ab") as log:
        process = subprocess.Popen(
            ["node", str(ROOT / "app/node_modules/next/dist/bin/next"), "start", "--hostname", "127.0.0.1", "--port", "3000"],
            cwd=ROOT / "app", env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
        )
    for _ in range(150):
        try:
            with urllib.request.urlopen("http://127.0.0.1:3000/login", timeout=2) as response:
                if response.status == 200:
                    return process
        except OSError:
            pass
        if process.poll() is not None:
            return None
        time.sleep(0.1)
    process.terminate()
    return None


os.kill(int(pid), signal.SIGTERM)
for _ in range(100):
    if not subprocess.run(["lsof", "-tiTCP:3000", "-sTCP:LISTEN"], capture_output=True).stdout:
        break
    time.sleep(0.1)
else:
    raise RuntimeError("旧前端没有释放 3000 端口")

(ROOT / "app/.next").rename(previous)
new.rename(ROOT / "app/.next")
process = start()
if process is None:
    # 回滚：换回旧构建再起服务。
    (ROOT / "app/.next").rename(new)
    previous.rename(ROOT / "app/.next")
    rollback = start()
    raise RuntimeError(f"新构建启动失败，已回滚旧构建（回滚进程 {rollback.pid if rollback else '未起来'}）")

record = {
    "previous_frontend_pid": int(pid),
    "frontend_pid": process.pid,
    "previous_build_id": old_id,
    "build_id": (ROOT / "app/.next/BUILD_ID").read_text().strip(),
    "api_pid": api_pid,
    "api_unchanged": api_pid == subprocess.check_output(["lsof", "-tiTCP:18743", "-sTCP:LISTEN"], text=True).strip(),
    "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
    "rollback": "停 frontend_pid 后，把 app/.next 换成本目录 previous_app_next，再按相同环境 next start",
}
(OUT / "processes.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
print(json.dumps(record, ensure_ascii=False))
