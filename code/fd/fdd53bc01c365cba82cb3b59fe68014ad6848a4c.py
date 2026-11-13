"""引擎无关的表格识别 A/B 判据（2026-10-25 选型验证）。

判据锚在**原件版面几何**：列身份取自 PDF 文字层里表头词的 x 锚点，行身份取自科目编码
词（或名称列的「合计」）的 y 锚点，两者都按相邻锚点中点切分。所以"金额是否落在正确
行列"是照原件判的，不照被评估引擎自己的表头切分判——否则把 9 列拆成 27 列的引擎会用
自己的错位表头给自己打分。

真值来自 scripts/intake_remediation_t0.json（T0-v1.1：验收方人工读渲染原页，不取自任
何系统输出）。本模块不使用"勾稽是否成立"作为指标，也不按解析结果的平整度打分。
"""

from __future__ import annotations

import json
import pathlib
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO = pathlib.Path(__file__).resolve().parents[2]
CORPUS = pathlib.Path.home() / "Desktop" / "决算测试上海市"
T0_PATH = REPO / "scripts" / "intake_remediation_t0.json"

#: 语义列 → 原件表头里的锚点词（用该词的水平中心当列锚点）
COLUMN_ANCHOR = {
    "total_actual": "本年收入",
    "income_total": "本年收入",
    "fiscal_allocation": "财政拨款",
    "superior_subsidy_income": "上级补助",
    "business_income": "事业收入",
    "operational_income": "经营收入",
    "subsidiary_income": "附属单位",
    "other_income": "其他收入",
    "expenditure_actual": "本年支出",
    "basic_expenditure": "基本支出",
    "project_expenditure": "项目支出",
}
TOTAL_ROW_TOKEN = "合计"

HARD_FAIL_VERDICTS = (
    "WRONG_COLUMN", "WRONG_ROW", "VALUE_SPLIT_ACROSS_CELLS", "TWO_AMOUNTS_ONE_CELL",
    "VALUE_MERGED_IN_CELL", "GRID_HOLE", "BLANK_READ_AS_VALUE", "MARK_READ_AS_VALUE",
    "CELL_PRESENT_TEXT_LOST", "VALUE_MISSING",
)


def norm_value(raw: Any) -> str:
    """金额格归一：全角转半角、去千分位与空白。"""
    if raw is None:
        return ""
    out = []
    for ch in str(raw):
        o = ord(ch)
        if 0xFF10 <= o <= 0xFF19:
            out.append(chr(ord("0") + o - 0xFF10))
        elif ch == "．":
            out.append(".")
        elif ch in ("－", "—", "–"):
            out.append("-")
        elif ch in (",", "，", " ", "\u3000", "\t", "\n"):
            continue
        else:
            out.append(ch)
    return "".join(out).strip()


def digits_of(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def amounts_in(text: str) -> List[str]:
    return re.findall(r"\d[\d,]*\.\d+", text or "")


@dataclass
class Cell:
    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    row: int = -1
    col: int = -1
    grid_id: int = -1

    @property
    def xc(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def yc(self) -> float:
        return (self.top + self.bottom) / 2.0


@dataclass
class Grid:
    """某引擎在某页的输出；cells 汇总该页所有表，页级判据不依赖表如何被切分。"""

    engine: str
    params: Dict[str, Any]
    page_number: int
    cells: List[Cell] = field(default_factory=list)
    table_bboxes: List[Tuple[float, float, float, float]] = field(default_factory=list)
    elapsed_s: float = 0.0
    note: str = ""

    def _by_grid(self, gid: int) -> List[Cell]:
        return [c for c in self.cells if c.grid_id == gid]

    def shape_of_largest_table(self) -> Tuple[int, int]:
        best = (0, 0)
        for gid in range(len(self.table_bboxes)):
            cs = self._by_grid(gid)
            if not cs:
                continue
            shape = (len({c.row for c in cs}), len({c.col for c in cs}))
            if shape[0] * shape[1] > best[0] * best[1]:
                best = shape
        return best

    def metrics(self) -> Dict[str, Any]:
        rows, cols = self.shape_of_largest_table()
        nonempty = [c for c in self.cells if norm_value(c.text)]
        two = [c for c in self.cells if len(amounts_in(c.text)) > 1]
        area = sum(max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1]) for b in self.table_bboxes)
        return {
            "engine": self.engine,
            "params": self.params,
            "arm": self.params.get("arm_name", ""),
            "page": self.page_number,
            "tables": len(self.table_bboxes),
            "largest_rows": rows,
            "largest_cols": cols,
            "cells": len(self.cells),
            "nonempty_cells": len(nonempty),
            "blank_ratio": round(1 - len(nonempty) / len(self.cells), 3) if self.cells else None,
            "cells_with_two_amounts": len(two),
            "covered_area_pt2": round(area, 1),
            "elapsed_s": round(self.elapsed_s, 3),
            "note": self.note,
        }


class PageGeometry:
    """语义列 → x 区间、真值行 → y 区间，全部由原件文字层推出。"""

    def __init__(self, words: Sequence[Dict[str, float]]):
        self.words = list(words)
        self.col_center: Dict[str, float] = {}
        self.col_band: Dict[str, Tuple[float, float]] = {}
        self._row_anchors: Optional[Dict[str, float]] = None

    def anchor_columns(self, keys: Sequence[str]) -> List[str]:
        """两遍：先按表头词锚定有表头的列，再插值无表头列（原件漏印表头，只能靠左右锚点）。"""
        unresolved: List[str] = []
        for i, key in enumerate(keys):
            token = COLUMN_ANCHOR.get(key)
            if not token:
                continue
            hits = [w for w in self.words if token in w["text"]]
            if hits:
                band = _merge_overlapping(hits)
                self.col_band[key] = band
                self.col_center[key] = (band[0] + band[1]) / 2.0
            else:
                unresolved.append(key)
        for i, key in enumerate(keys):
            if COLUMN_ANCHOR.get(key) or key in self.col_center:
                continue
            if key != "UNLABELED":
                unresolved.append(key)
                continue
            left = next((self.col_center[k] for k in reversed(keys[:i]) if k in self.col_center), None)
            right = next((self.col_center[k] for k in keys[i + 1:] if k in self.col_center), None)
            if left is not None and right is not None:
                self.col_center[key] = (left + right) / 2.0
            else:
                unresolved.append(key)
        return unresolved

    def column_partition(self) -> Dict[str, Tuple[float, float]]:
        ordered = sorted(self.col_center.items(), key=lambda kv: kv[1])
        out: Dict[str, Tuple[float, float]] = {}
        for i, (key, c) in enumerate(ordered):
            left = -1e9 if i == 0 else (ordered[i - 1][1] + c) / 2.0
            right = 1e9 if i == len(ordered) - 1 else (c + ordered[i + 1][1]) / 2.0
            out[key] = (left, right)
        return out

    def row_anchors(self) -> Dict[str, float]:
        """行锚点只在**最左金额列以左**的标签区取，否则两层表头里的「合计」会被当成合计行。"""
        if self._row_anchors is None:
            limit = min((b[0] for b in self.col_band.values()), default=1e9)
            out: Dict[str, float] = {}
            for w in self.words:
                if w["x1"] > limit:
                    continue
                text = w["text"].strip()
                yc = (w["top"] + w["bottom"]) / 2.0
                if text == TOTAL_ROW_TOKEN:
                    out.setdefault("total", yc)
                    continue
                code = norm_value(text)
                if re.fullmatch(r"\d{1,7}", code):
                    out.setdefault(code, yc)
            self._row_anchors = out
        return self._row_anchors

    def row_band(self, row_code: str) -> Optional[Tuple[float, float]]:
        anchors = self.row_anchors()
        if row_code not in anchors:
            return None
        ordered = sorted(anchors.items(), key=lambda kv: kv[1])
        keys = [k for k, _ in ordered]
        ys = [v for _, v in ordered]
        c = anchors[row_code]
        i = keys.index(row_code)
        lo = -1e9 if i == 0 else (ys[i - 1] + c) / 2.0
        hi = 1e9 if i == len(keys) - 1 else (c + ys[i + 1]) / 2.0
        return (lo, hi)


def _merge_overlapping(hits: Sequence[Dict[str, float]]) -> Tuple[float, float]:
    """两层表头（「本年收入」＋「合计」）水平重叠时并成一个带；取最靠左的重叠簇。"""
    bands: List[Tuple[float, float]] = []
    for w in sorted(hits, key=lambda x: x["x0"]):
        if bands and w["x0"] <= bands[-1][1] - 1.0:
            bands[-1] = (bands[-1][0], max(bands[-1][1], w["x1"]))
        else:
            bands.append((w["x0"], w["x1"]))
    return bands[0]


def grid_pdfplumber(page, params: Dict[str, Any], page_number: int) -> Grid:
    settings = dict(
        vertical_strategy=params.get("vertical_strategy", "lines"),
        horizontal_strategy=params.get("horizontal_strategy", "lines"),
        snap_tolerance=params.get("snap_tolerance", 3),
        join_tolerance=params.get("join_tolerance", 3),
        edge_min_length=params.get("edge_min_length", 3),
    )
    for k in ("text_tolerance", "min_words_vertical", "min_words_horizontal", "intersection_tolerance"):
        if k in params:
            settings[k] = params[k]
    grid = Grid(engine="pdfplumber", params=params, page_number=page_number)
    started = time.perf_counter()
    for gi, table in enumerate(page.find_tables(table_settings=settings)):
        grid.table_bboxes.append(tuple(table.bbox))
        for r, row in enumerate(table.extract() or []):
            for c, text in enumerate(row):
                bbox = _plumber_cell_bbox(table, r, c)
                if bbox is None:
                    continue
                grid.cells.append(Cell(text=text or "", x0=bbox[0], top=bbox[1], x1=bbox[2],
                                       bottom=bbox[3], row=r, col=c, grid_id=gi))
    grid.elapsed_s = time.perf_counter() - started
    return grid


def _plumber_cell_bbox(table, r: int, c: int) -> Optional[Tuple[float, float, float, float]]:
    try:
        cells = table.rows[r].cells
        bb = cells[c] if c < len(cells) else None
    except (IndexError, AttributeError):
        return None
    return tuple(bb) if bb else None


def _cells_in_row(grid: Grid, yband: Tuple[float, float]) -> List[Cell]:
    return [c for c in grid.cells if yband[0] <= c.yc < yband[1]]


def _cells_at(grid: Grid, yband: Tuple[float, float], xband: Tuple[float, float]) -> List[Cell]:
    return [c for c in _cells_in_row(grid, yband) if xband[0] <= c.xc < xband[1]]


def column_of(geom: PageGeometry, partition: Dict[str, Tuple[float, float]], x: float) -> str:
    for key, (lo, hi) in partition.items():
        if lo <= x < hi:
            return key
    return "outside"


def score_cell(grid: Grid, geom: PageGeometry, partition: Dict[str, Tuple[float, float]],
               row_code: str, col_key: str, truth: str) -> Dict[str, Any]:
    yband = geom.row_band(row_code)
    xband = partition.get(col_key)
    res: Dict[str, Any] = {"row": row_code, "column": col_key, "truth": truth}
    if yband is None or xband is None:
        res["verdict"] = "GEOMETRY_UNRESOLVED"
        res["detail"] = "原件几何定位不到该行列，样本不计入统计"
        return res

    if truth in ("BLANK", "DASH", "UNLABELED"):
        here = _cells_at(grid, yband, xband)
        empties = [c for c in here if not norm_value(c.text)]
        if empties and truth == "BLANK":
            res["verdict"] = "BLANK_OK"
            res["detail"] = f"{len(empties)} 格覆盖该位置且为空"
        elif here:
            res["verdict"] = "BLANK_READ_AS_VALUE" if truth == "BLANK" else "MARK_READ_AS_VALUE"
            res["detail"] = "；".join(repr(c.text) for c in here[:3])
        else:
            res["verdict"] = "GRID_HOLE"
            res["detail"] = "无单元格覆盖该位置：分不清原件空白与漏读"
        return res

    target = norm_value(truth)
    here = _cells_at(grid, yband, xband)
    hits = [c for c in here if norm_value(c.text) == target]
    if hits:
        res["verdict"] = "OK"
        res["cell"] = {"grid_id": hits[0].grid_id, "row": hits[0].row, "col": hits[0].col}
        return res
    row_cells = _cells_in_row(grid, yband)
    if any(norm_value(c.text) == target for c in row_cells):
        res["verdict"] = "WRONG_COLUMN"
        res["detail"] = "、".join(f"{column_of(geom, partition, c.xc)}@x{c.xc:.0f}"
                                 for c in row_cells if norm_value(c.text) == target)
        return res
    if any(norm_value(c.text) == target for c in grid.cells):
        res["verdict"] = "WRONG_ROW"
        res["detail"] = "金额在页上其他行"
        return res
    dg = digits_of(target)
    for a, b in zip(sorted(row_cells, key=lambda c: c.x0), sorted(row_cells, key=lambda c: c.x0)[1:]):
        if dg and len(dg) > 3 and digits_of(a.text) + digits_of(b.text) == dg:
            res["verdict"] = "VALUE_SPLIT_ACROSS_CELLS"
            res["detail"] = f"{a.text!r} + {b.text!r}"
            return res
    if here:
        if any(target and target in norm_value(c.text) for c in here):
            res["verdict"] = "VALUE_MERGED_IN_CELL"
            res["detail"] = next(repr(c.text) for c in here if target in norm_value(c.text))
        elif any(len(amounts_in(c.text)) > 1 for c in here):
            res["verdict"] = "TWO_AMOUNTS_ONE_CELL"
            res["detail"] = next(repr(c.text) for c in here if len(amounts_in(c.text)) > 1)
        elif any(not norm_value(c.text) for c in here):
            res["verdict"] = "CELL_PRESENT_TEXT_LOST"
            res["detail"] = f"该位置有 {len(here)} 格且为空/缺字"
        else:
            res["verdict"] = "CELL_PRESENT_TEXT_DIFFERENT"
            res["detail"] = "；".join(repr(c.text) for c in here[:3])
        return res
    res["verdict"] = "VALUE_MISSING"
    res["detail"] = "整页无该金额的归一文本"
    return res


def load_t0() -> Dict[str, Any]:
    return json.loads(T0_PATH.read_text())


def corpus_path(rel: str) -> pathlib.Path:
    return CORPUS / rel


def page_words(pdf_path: pathlib.Path, page_number: int) -> List[Dict[str, float]]:
    import pdfplumber
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_number - 1]
        return [{"text": w["text"], "x0": w["x0"], "x1": w["x1"],
                 "top": w["top"], "bottom": w["bottom"]} for w in page.extract_words()]


def header_trace(grid: Grid, col_key: str, cell: Dict[str, int]) -> str:
    """金额格往上同一条**网格列**里能不能直接看到它的表头词。

    这是 9 列拆成 27 列的真实危害：几何上金额仍在正确 x 带里，但表头落在别的子列，
    下游按"同列即同身份"映射就会失败。x 带判据看得见、这条判据才看得见危害。
    """
    token = COLUMN_ANCHOR.get(col_key)
    if token is None:
        return "原件无该列表头" if col_key == "UNLABELED" else "无锚点词"
    same = [c for c in grid.cells
            if c.grid_id == cell["grid_id"] and c.col == cell["col"] and c.row < cell["row"]
            and token in (c.text or "")]
    return "表头同列" if same else "表头异列"


def score_case(case: Dict[str, Any], grids: Sequence[Grid]) -> Dict[str, Any]:
    pdf_path = corpus_path(case["file"])
    words = page_words(pdf_path, case["page"])
    keys: List[str] = []
    for row in case["rows"]:
        for k in row["cells"]:
            if k not in keys:
                keys.append(k)
    geom = PageGeometry(words)
    unresolved_cols = geom.anchor_columns(keys)
    partition = geom.column_partition()
    unresolved_rows = [r["code"] for r in case["rows"] if geom.row_band(r["code"]) is None]

    out: Dict[str, Any] = {
        "case_id": case["id"], "category": case["category"], "file": case["file"],
        "page": case["page"], "table_code": case.get("table_code"),
        "expectation_batches": case.get("batches"),
        "unresolved_columns": unresolved_cols, "unresolved_rows": unresolved_rows,
        "arms": {},
    }
    if unresolved_cols or unresolved_rows:
        out["case_invalid"] = True
    for grid in grids:
        verdicts = [score_cell(grid, geom, partition, r["code"], k, v)
                    for r in case["rows"] for k, v in r["cells"].items()]
        tally: Dict[str, int] = {}
        header: Dict[str, int] = {}
        for v in verdicts:
            tally[v["verdict"]] = tally.get(v["verdict"], 0) + 1
            if v["verdict"] == "OK":
                t = header_trace(grid, v["column"], v["cell"])
                header[t] = header.get(t, 0) + 1
        arm = f"{grid.engine}|{grid.params.get('arm_name') or _param_tag(grid.params)}"
        out["arms"][arm] = {
            "tally": tally,
            "header_trace": header,
            "hard_fail": [v for v in verdicts if v["verdict"] in HARD_FAIL_VERDICTS],
            "grid_metrics": grid.metrics(),
        }
    return out


def _param_tag(params: Dict[str, Any]) -> str:
    return ",".join(f"{k}={v}" for k, v in sorted(params.items()) if k != "arm_name")
