"""在副本中构建前端（不碰正在运行的 app/.next）；环境与 3000 的启动脚本一致。"""
import os
from pathlib import Path
import subprocess
import sys

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
APP = OUT / "build-workspace/app"

env = os.environ.copy()
env.update({key: value for key, value in dotenv_values(ROOT / ".env").items() if value is not None})
env.update(BACKEND_URL="http://127.0.0.1:18743", GOVBUDGET_BACKEND_PORT="18743", NEXT_TELEMETRY_DISABLED="1")
with (OUT / "build.log").open("wb") as log:
    result = subprocess.run(
        ["node", str(APP / "node_modules/next/dist/bin/next"), "build"],
        cwd=APP, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
    )
print("build exit", result.returncode, "BUILD_ID exists", (APP / ".next/BUILD_ID").is_file())
sys.exit(result.returncode)
