#!/usr/bin/env python3
"""从候选工作区 cwd 启动 API，并把真实模块来源写成身份文件。"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main() -> int:
    sys.path.insert(0, os.getcwd())
    import api.main
    import src

    identity = {
        "role": "api",
        "cwd": os.getcwd(),
        "executable": sys.executable,
        "sys_path_head": sys.path[:8],
        "api_main_file": api.main.__file__,
        "src_file": src.__file__,
        "git_head": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                                   text=True).stdout.strip(),
        "git_status": subprocess.run(["git", "status", "--porcelain"], capture_output=True,
                                     text=True).stdout.strip().splitlines(),
        "files": {
            "api/main.py": sha("api/main.py"),
            "api/routes/reviews.py": sha("api/routes/reviews.py"),
            "src/services/review_lifecycle_service.py": sha("src/services/review_lifecycle_service.py"),
            "src/schemas/review_lifecycle.py": sha("src/schemas/review_lifecycle.py"),
            "src/engine/checklist_text_rules.py": sha("src/engine/checklist_text_rules.py"),
        },
        "env": {key: os.environ.get(key) for key in
                ("DATABASE_URL", "UPLOAD_DIR", "ORG_DATA_DIR", "AUDIT_LOG_PATH",
                 "JOB_QUEUE_ROLE", "JOB_QUEUE_ENABLED", "TESTING")},
    }
    out = Path(os.environ["IDENTITY_OUT"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(identity, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    import uvicorn
    uvicorn.run(api.main.app, host="127.0.0.1", port=int(os.environ.get("PORT", "18848")),
                log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
