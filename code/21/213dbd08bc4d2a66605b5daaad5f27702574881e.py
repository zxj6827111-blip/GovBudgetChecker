#!/usr/bin/env python3
"""Index every saved historical/current evidence file with its SHA256."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

RUN = Path(__file__).resolve().parents[1]


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    index = {"kind": "real_painpoint_diag_evidence_index", "run_dir": str(RUN), "groups": {}}
    for group in ("historical", "current"):
        base = RUN / "evidence" / group
        rows = []
        for path in sorted(base.rglob("*")):
            if path.is_file() and not path.name.startswith("."):
                rows.append({"path": str(path.relative_to(RUN)), "bytes": path.stat().st_size, "sha256": sha(path)})
        index["groups"][group] = rows
    for path in sorted(RUN.glob("*.json")) + sorted(RUN.glob("*.md")) + sorted(RUN.glob("*.txt")) + sorted(RUN.glob("*.csv")):
        if path.name in {"CURRENT_INDEX.json", "RUN_IDENTITY.json"}:
            continue
        index.setdefault("top_level", []).append({"path": path.name, "bytes": path.stat().st_size, "sha256": sha(path)})
    (RUN / "CURRENT_INDEX.json").write_text(json.dumps(index, ensure_ascii=False, indent=2))
    print(json.dumps({k: len(v) for k, v in index["groups"].items()} | {"top_level": len(index.get("top_level", []))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
