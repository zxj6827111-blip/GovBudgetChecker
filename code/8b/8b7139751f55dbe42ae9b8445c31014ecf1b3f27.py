"""把各臂在指定页的网格前几行打印出来，用于核对"表头异列"到底是引擎缺陷还是判据误判。"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import pdfplumber  # noqa: E402
from ab_lib import CORPUS, grid_pdfplumber, load_t0  # noqa: E402

MODE = sys.argv[1] if len(sys.argv) > 1 else "camelot"
REL = sys.argv[2] if len(sys.argv) > 2 else "2022/上海市交通委员会/上海市交通委员会2022年度部门决算.pdf"
PAGE = int(sys.argv[3]) if len(sys.argv) > 3 else 9

path = pathlib.Path(str(CORPUS / REL))
with pdfplumber.open(path) as pdf:
    page = pdf.pages[PAGE - 1]
    height = float(page.height)
    print(f"页面 {PAGE} 尺寸 {page.width:.1f}x{height:.1f}")
    for arm in ([{"arm_name": "plumber-snap3", "snap_tolerance": 3},
                 {"arm_name": "plumber-snap6", "snap_tolerance": 6},
                 {"arm_name": "plumber-snap8", "snap_tolerance": 8}]):
        g = grid_pdfplumber(page, arm, PAGE)
        rows = {}
        for c in g.cells:
            rows.setdefault((c.grid_id, c.row), {})[c.col] = (c.text, c.x0, c.x1)
        print(f"\n--- {arm['arm_name']} 表{len(g.table_bboxes)} 最大列数 "
              f"{g.metrics()['largest_cols']}")
        for (gi, r) in sorted(rows)[:5]:
            cells = rows[(gi, r)]
            line = " | ".join(f"[{c}]{(cells[c][0] or '·')[:12]}@{cells[c][1]:.0f}-{cells[c][2]:.0f}"
                              for c in sorted(cells))
            print(f"  r{r}: {line}")

if MODE == "camelot":
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import camelot
    tables = camelot.read_pdf(str(path), pages=str(PAGE), flavor="lattice", suppress_stdout=True)
    for t in tables:
        print(f"\n--- camelot lattice shape={t.shape} bbox={tuple(round(v,1) for v in t._bbox)}")
        for r in range(min(5, t.shape[0])):
            vals = t.df.values.tolist()[r]
            cs = t.cells[r]
            line = " | ".join(f"[{c}]{(str(vals[c]) or '·')[:12]}@{cs[c].x1:.0f}-{cs[c].x2:.0f}"
                              for c in range(len(vals)) if True)
            print(f"  r{r}: {line}")
