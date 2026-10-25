"""IR-18：把 T0 冻结真值转成逐格真值种子（truth_source=t0_frozen）。

T0 的 rows 以“科目编码 + 指标”组织，不含列号；这里原样保留，不补造列坐标。
合成样本的物理页按生成脚本的约定为第 1 页。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_T0 = ROOT / "scripts/intake_remediation_t0.json"
DEFAULT_CORPUS = Path.home() / "Desktop/决算测试上海市"
DEFAULT_T0_DIR = ROOT / "outputs/intake_remediation_20261024/t0"
DEFAULT_OUT = ROOT / "outputs/intake_remediation_20261024/ir18_cell_truth/truth"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def first_table_page(path: Path) -> int:
    """合成样本的封面在第 1 页；按原件表题定位表格页，不读取解析结果。"""
    try:
        import pymupdf

        with pymupdf.open(path) as pdf:
            for index, page in enumerate(pdf):
                text = page.get_text() or ""
                if "决算表" in text or "预算表" in text:
                    return index + 1
    except Exception:
        pass
    return 1


def convert(item: dict[str, Any], *, synthetic: bool, corpus: Path, t0_dir: Path, out: Path) -> dict[str, Any] | None:
    rel = str(item.get("file") or "")
    path = (t0_dir / rel) if synthetic else (corpus / rel)
    if not path.exists():
        return None
    page = int(item.get("page") or 0) or (first_table_page(path) if synthetic else 1)
    sample_id = f"t0-{item['id']}-{sha256(path)[:12]}-p{page}"
    truth = {
        "sample_id": sample_id,
        "t0_id": item.get("id"),
        "source_sha256": sha256(path),
        "file": rel,
        "physical_page": page,
        "table_code": str(item.get("table_code") or "UNKNOWN"),
        "truth_source": "t0_frozen",
        "annotator": "验收方（Claude）",
        "annotated_at": "2026-10-24",
        "whole_table": True,
        "rows": item.get("rows") or [],
        "amount_cells": [],
        "column_identity": {},
        "notes": f"T0-v1.1 {item.get('category') or ''}；真值取自渲染原页人工读取，不取自系统输出",
        "render": None,
        "batch": item.get("batches") or {},
        "synthetic": synthetic,
        "unit": item.get("unit"),
    }
    (out / f"{sample_id}.json").write_text(json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8")
    return truth


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--t0", type=Path, default=DEFAULT_T0)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--t0-dir", type=Path, default=DEFAULT_T0_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    manifest = json.loads(args.t0.read_text(encoding="utf-8"))
    args.out.mkdir(parents=True, exist_ok=True)
    converted, skipped = [], []
    for item in manifest.get("cells") or []:
        truth = convert(item, synthetic=False, corpus=args.corpus, t0_dir=args.t0_dir, out=args.out)
        (converted if truth else skipped).append(item.get("id"))
    for item in manifest.get("synthetic") or []:
        truth = convert(item, synthetic=True, corpus=args.corpus, t0_dir=args.t0_dir, out=args.out)
        (converted if truth else skipped).append(item.get("id"))
    summary = {
        "t0_version": manifest.get("version"),
        "converted": converted,
        "missing_source": skipped,
        "count": len(converted),
    }
    (args.out.parent / "seed_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
