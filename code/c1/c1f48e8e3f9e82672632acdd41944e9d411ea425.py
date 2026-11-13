"""定位真实失败任务 payload 中 Decimal 的产生路径（只读探针）。

用法（cwd 必须是候选工作区）：
    cd <candidate-worktree>
    /path/to/venv/bin/python <this-script> <job_dir> <out_json>

纪律：必须先断言所有导入模块来自候选工作区；不得靠 PYTHONPATH 掩盖 cwd 遮蔽。
"""
from __future__ import annotations

from decimal import Decimal
import json
import os
from pathlib import Path
import sys


def _assert_candidate(root: Path) -> None:
    # 脚本位于输出目录，sys.path[0] 是脚本目录；显式把候选工作区放到最前面，
    # 再断言实际导入的模块文件确实来自候选（不接受 main 仓遮蔽）。
    sys.path.insert(0, str(root))
    import src.engine.pipeline as pipeline
    import src.engine.fact_context as fact_context

    for module in (pipeline, fact_context):
        actual = Path(module.__file__).resolve()
        if root.resolve() not in actual.parents:
            raise SystemExit(f"FATAL: module {module.__name__} imported from {actual}, not candidate {root}")
    print(f"[identity] pipeline={pipeline.__file__}")


def _walk(value, path="$"):
    if isinstance(value, Decimal):
        yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _walk(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _walk(item, f"{path}[{index}]")


def main() -> int:
    job_dir = Path(sys.argv[1]).resolve()
    out_path = Path(sys.argv[2]).resolve()
    root = Path.cwd().resolve()

    _assert_candidate(root)

    import pdfplumber
    from src.engine.rules_v33 import build_document
    from src.engine.fact_context import FactContext
    from src.engine.pipeline import build_issues_payload
    from src.services.pdf_page_extract import (
        extract_tables_from_page,
        extract_visible_text_from_page,
    )

    pdf_path = next(p for p in job_dir.iterdir() if p.suffix.lower() == ".pdf")
    page_texts, page_tables = [], []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            page_texts.append(extract_visible_text_from_page(page))
            page_tables.append(extract_tables_from_page(page))

    doc = build_document(
        path=str(pdf_path),
        page_texts=page_texts,
        page_tables=page_tables,
        filesize=pdf_path.stat().st_size,
    )

    fact_context_file = job_dir / "fact_context.json"
    if fact_context_file.exists():
        doc.fact_context = FactContext.from_dict(json.loads(fact_context_file.read_text(encoding="utf-8")))
        print(f"[fact_context] facts={len(doc.fact_context.facts)}")
    else:
        print("[fact_context] MISSING")

    payload = build_issues_payload(doc, use_ai_assist=False, report_kind="final")
    hits = list(_walk(payload))
    print(f"[payload] issues={len(payload.get('issues', {}).get('all', []))} receipts={len(payload.get('fact_receipts') or [])}")
    print(f"[decimal] hits={len(hits)}")
    for path, value in hits[:50]:
        print(f"  {path} = {value!r}")

    out = {
        "job_dir": str(job_dir),
        "pdf": str(pdf_path),
        "payload_decimal_hits": [{"path": p, "value": str(v)} for p, v in hits],
        "decimal_issue_producers": [
            {
                "rule_id": item.get("rule_id"),
                "title": item.get("title"),
                "page": item.get("page_number"),
                "diff": str((item.get("location") or {}).get("diff")),
                "location_keys": sorted((item.get("location") or {}).keys()),
            }
            for item in payload.get("issues", {}).get("error", [])
            if isinstance((item.get("location") or {}).get("diff"), Decimal)
        ],
        "issue_count": len(payload.get("issues", {}).get("all", [])),
        "receipt_count": len(payload.get("fact_receipts") or []),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[written] {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
