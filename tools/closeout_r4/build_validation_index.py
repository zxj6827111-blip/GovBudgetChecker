#!/usr/bin/env python3
"""生成交付包哈希索引；排除索引自身与 __pycache__/pyc 运行缓存。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "validation_index.json"
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", ".next"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}


def main() -> int:
    files = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if path.suffix in EXCLUDE_SUFFIXES:
            continue
        if path == INDEX:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        files.append({"path": str(rel), "bytes": path.stat().st_size, "sha256": digest})
    payload = {
        "index_kind": "sha256-manifest",
        "note": "排除 validation_index.json 自身与 __pycache__/pyc 运行缓存。",
        "root": str(ROOT),
        "file_count": len(files),
        "files": files,
    }
    INDEX.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"files": len(files), "index": str(INDEX)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
