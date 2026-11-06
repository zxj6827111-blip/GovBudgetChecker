#!/usr/bin/env python3
"""只读旧采集，生成有版本与哈希的新适配工件；不签收业务结果。"""
import argparse
import gzip
import json
from pathlib import Path
import shutil
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.effect_acceptance_v3 import load_collection, sha
from scripts.acceptance_artifacts import ADAPTER_VERSION, canonical


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("输出目录已存在，拒绝覆盖")
    rows = load_collection(args.source)
    args.out.mkdir(parents=True)
    (args.out / "records").mkdir()
    shutil.copytree(args.source / "status", args.out / "status")
    shutil.copyfile(args.source / "meta.json", args.out / "meta.json")
    audit = []
    for sid, row in rows.items():
        raw = row.pop("_raw_status")
        audit.append({"sample_id": sid, "raw_formal_count": len(raw["review_report"]["issues"]),
                      "adapted_formal_count": len(row["findings"]), "binding": row["source_binding"],
                      "record_sha256": sha(args.source / "records" / (sid + ".json.gz")),
                      "status_sha256": sha(args.source / "status" / (sid + ".status.json.gz"))})
        with gzip.open(args.out / "records" / (sid + ".json.gz"), "wt", encoding="utf-8") as handle:
            json.dump(row, handle, ensure_ascii=False)
    report = {"adapter": ADAPTER_VERSION, "source": str(args.source.resolve()),
              "samples": len(audit), "raw_formal_total": sum(r["raw_formal_count"] for r in audit),
              "adapted_formal_total": sum(r["adapted_formal_count"] for r in audit), "audit": audit}
    (args.out / "adapter_audit.json").write_text(canonical(report), encoding="utf-8")
    print(canonical({k: v for k, v in report.items() if k != "audit"}))


if __name__ == "__main__":
    main()
