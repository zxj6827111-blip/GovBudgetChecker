#!/usr/bin/env python3
"""从候选工作区 cwd 启动 worker，并记录真实模块来源。"""
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
    import api.worker

    identity = {
        "role": "worker",
        "cwd": os.getcwd(),
        "executable": sys.executable,
        "sys_path_head": sys.path[:6],
        "api_worker_file": api.worker.__file__,
        "git_head": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                                   text=True).stdout.strip(),
        "git_status": subprocess.run(["git", "status", "--porcelain"], capture_output=True,
                                     text=True).stdout.strip().splitlines(),
        "files": {
            "api/worker.py": sha("api/worker.py"),
            "src/services/review_lifecycle_service.py": sha("src/services/review_lifecycle_service.py"),
            "src/engine/checklist_text_rules.py": sha("src/engine/checklist_text_rules.py"),
        },
        "env": {key: os.environ.get(key) for key in
                ("DATABASE_URL", "UPLOAD_DIR", "ORG_DATA_DIR", "JOB_QUEUE_ROLE",
                 "JOB_QUEUE_ENABLED", "TESTING")},
    }
    out = Path(os.environ["IDENTITY_OUT"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(identity, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    api.worker.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
