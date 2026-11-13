"""扫描件臂：实测已在跑的 PP-StructureV3 服务，并按 GPT6 的三层归因拆开看。

层 1 引擎：服务是否返回表格对象、pred_html 的行列、单元格框与文字 token、置信度。
层 2 转换：ocr_service.protocol.map_table_cells 能不能把 HTML 与几何对上（mapped / ambiguous）。
层 3 规则：不在本脚本内，用现有回执字段另行统计。

直连 /ocr（不带 tile_context），所以客户端的 tile 校验守卫这一层本探针不覆盖，报告里会写明。
"""

from __future__ import annotations

import json
import pathlib
import sys
import time

sys.path.insert(0, "/Volumes/workSSD/software development/GovBudgetChecker")
import httpx  # noqa: E402
from ocr_service.protocol import map_table_cells, PROTOCOL_VERSION  # noqa: E402
from src.services.ocr_pipeline import DEFAULT_DPI, _render_pages_png  # noqa: E402

URL = "http://127.0.0.1:9010/ocr"


def probe(pdf_rel: str, pages: list[int]) -> dict:
    pdf_path = str(pathlib.Path.home() / "Desktop" / "决算测试上海市" / pdf_rel)
    images, transforms = _render_pages_png(pdf_path, pages, dpi=DEFAULT_DPI)
    out = {"file": pdf_rel, "dpi": DEFAULT_DPI, "pages": []}
    for page_number, png in zip(pages, images):
        if not png:
            continue
        started = time.perf_counter()
        try:
            r = httpx.post(URL, files={"image": (f"p{page_number}.png", png, "image/png")},
                           timeout=600.0)
            r.raise_for_status()
            payload = r.json()
        except Exception as exc:
            out["pages"].append({"page": page_number, "error": f"{type(exc).__name__}: {exc}",
                                 "seconds": round(time.perf_counter() - started, 1)})
            continue
        elapsed = time.perf_counter() - started
        tables = payload.get("tables") or []
        page_row = {
            "page": page_number, "seconds": round(elapsed, 1),
            "schema_version": payload.get("schema_version"),
            "render_px": (transforms.get(page_number, {}) or {}).get("render_size_px"),
            "text_blocks": len(payload.get("text_blocks") or payload.get("blocks") or []),
            "tables": len(tables),
            "engine": [],
        }
        for t in tables:
            mapped = map_table_cells(t) if "pred_html" in t else {}
            page_row["engine"].append({
                "html_rows": t.get("row_count"), "html_cols": t.get("col_count"),
                "cells_box": len(t.get("cell_box_list") or []),
                "pred_tokens": len(t.get("table_ocr_pred") or []),
                "mapping_status": mapped.get("mapping_status"),
                "mapping_reasons": (mapped.get("mapping_reasons") or [])[:2],
                "mapped_cells": len(mapped.get("cells") or []),
                "cells_with_bbox": sum(1 for c in (mapped.get("cells") or []) if c.get("bbox") or c.get("render_bbox")),
                "cells_with_text": sum(1 for c in (mapped.get("cells") or [])
                                       if (c.get("text") or "").strip()),
            })
        out["pages"].append(page_row)
        print(json.dumps(page_row, ensure_ascii=False)[:900], flush=True)
    return out


if __name__ == "__main__":
    rel = sys.argv[1]
    pgs = [int(x) for x in sys.argv[2].split(",")]
    res = probe(rel, pgs)
    print("\nPROTOCOL_VERSION", PROTOCOL_VERSION)
    pathlib.Path("/Volumes/workSSD/software development/GovBudgetChecker/scratch/engine_ab_20261025/"
                 f"scan_probe_{pgs[0]}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
