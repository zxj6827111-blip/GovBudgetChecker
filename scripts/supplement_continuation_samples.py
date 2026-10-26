"""IR-18 续页补充样本：从指定材料中提取紧邻已知表种的 UNKNOWN 续页。

batch2 的 parse.jsonl 只导出逻辑合并后的表，续页在汇总层面不可见；
这里直接读取解析对象的 physical_fragments，取“本页表种 UNKNOWN、前一页
是已知表种”的片段作为续页样本，并渲染原页、生成空真值模板。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DEFAULT_CORPUS = Path.home() / "Desktop/决算测试上海市"
DEFAULT_OUT = ROOT / "outputs/intake_remediation_20261024/ir18_cell_truth"
DEFAULT_FILES = [
    "2022/上海市交通委员会/上海市交通委员会2022年度部门决算.pdf",
    "2022/上海市教育委员会/上海市材料工程学校2022年度决算.pdf",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract(pdf_path: Path) -> list[dict[str, Any]]:
    from src.services.pdf_parser import PDFParser

    artifact = PDFParser(None).build_parse_artifact(str(pdf_path))
    known_by_page: dict[int, str] = {}
    for fragment in artifact.physical_fragments:
        if not str(fragment.table_code).startswith("UNKNOWN"):
            known_by_page[int(fragment.page_number)] = str(fragment.table_code)
    samples = []
    for fragment in artifact.physical_fragments:
        page = int(fragment.page_number)
        code = str(fragment.table_code)
        if not code.startswith("UNKNOWN"):
            continue
        previous_code = known_by_page.get(page - 1)
        if not previous_code:
            continue
        samples.append({
            "physical_page": page,
            "system_table_code": code,
            "continuation_of": previous_code,
            "rows": len(fragment.rows),
            "cols": max((len(row) for row in fragment.rows), default=0),
            "method": str(fragment.extraction_method),
        })
    return samples


def render(pdf_path: Path, page: int, target: Path) -> None:
    import pymupdf

    with pymupdf.open(pdf_path) as pdf:
        pix = pdf[page - 1].get_pixmap(dpi=150)
    target.write_bytes(pix.tobytes("png"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--file", action="append")
    args = parser.parse_args()
    files = args.file or DEFAULT_FILES
    render_dir = args.out / "render"
    truth_dir = args.out / "truth"
    render_dir.mkdir(parents=True, exist_ok=True)
    truth_dir.mkdir(parents=True, exist_ok=True)
    supplement = []
    for rel in files:
        pdf_path = args.corpus / rel
        if not pdf_path.exists():
            print(json.dumps({"file": rel, "error": "missing"}, ensure_ascii=False))
            continue
        digest = sha256(pdf_path)
        for item in extract(pdf_path):
            sample_id = f"cont-{digest[:12]}-p{item['physical_page']}-{item['continuation_of']}"
            render_path = render_dir / f"{sample_id}.png"
            render(pdf_path, item["physical_page"], render_path)
            truth = {
                "sample_id": sample_id,
                "source_sha256": digest,
                "file": rel,
                "physical_page": item["physical_page"],
                "table_code": item["continuation_of"],
                "system_table_code": item["system_table_code"],
                "continuation": True,
                "continuation_of": item["continuation_of"],
                "truth_source": "unannotated",
                "annotator": None,
                "annotated_at": None,
                "whole_table": None,
                "rows": [],
                "amount_cells": [],
                "column_identity": {},
                "notes": "续页样本：标注时按续接表种填写行与金额，不补列名",
                "render": str(render_path.relative_to(args.out)),
                "instructions": (
                    "这是跨页表的续页。按原页填写续接的数据行；"
                    "表头若在原页缺失，只记 notes，不补列名。"
                ),
            }
            (truth_dir / f"{sample_id}.json").write_text(
                json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8")
            supplement.append(truth)
    sampling_path = args.out / "sampling.json"
    if sampling_path.exists():
        sampling = json.loads(sampling_path.read_text(encoding="utf-8"))
    else:
        sampling = {"samples": []}
    sampling["continuation_supplement"] = supplement
    sampling_path.write_text(json.dumps(sampling, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "supplement_samples": len(supplement),
        "by_file": {rel: sum(1 for item in supplement if item["file"] == rel) for rel in files},
        "samples": [{key: item[key] for key in ("sample_id", "file", "physical_page",
                                                 "table_code", "system_table_code")}
                    for item in supplement],
    }
    (args.out / "continuation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
